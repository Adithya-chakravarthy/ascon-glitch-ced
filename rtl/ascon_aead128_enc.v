// Ascon-AEAD128 encryption, one permutation round per clock cycle.
// AD and plaintext are whole 16-byte blocks (0..15 each); padding is added here.
// Blocks are {W1, W0} with W0 = bytes 0..7 little-endian.
//
// CHECK: 0 none, 1 S-box parity, 5 S-box interleaved parity, 6 S-box CRC-3,
//        7 S-box CRC-3 with registered compare, 2 global parity,
//        3 group parity (ascon_gpar_check from gen_gpar.py).
// alarm stays set until the next start.
module ascon_aead128_enc #(
    parameter integer CHECK = 0
) (
    input  wire         clk,
    input  wire         rst,
    input  wire         start,
    input  wire [127:0] key,
    input  wire [127:0] nonce,
    input  wire [3:0]   ad_blocks,
    input  wire [3:0]   pt_blocks,
    input  wire [127:0] din,
    input  wire         din_valid,
    output wire         din_ready,
    output reg  [127:0] dout,
    output reg          dout_valid,
    output reg  [127:0] tag,
    output reg          done,
    output wire         busy,
    output wire         alarm,
    output wire [319:0] state_o
);
    localparam [63:0] IV = 64'h00001000808C0001;   // Ascon-AEAD128

    // FSM
    localparam [3:0] S_IDLE     = 4'd0,
                     S_P_INIT   = 4'd1,
                     S_INIT_KEY = 4'd2,
                     S_AD_WAIT  = 4'd3,
                     S_P_AD     = 4'd4,
                     S_AD_PAD   = 4'd5,
                     S_P_ADPAD  = 4'd6,
                     S_DOMSEP   = 4'd7,
                     S_PT_WAIT  = 4'd8,
                     S_P_PT     = 4'd9,
                     S_PT_PAD   = 4'd10,
                     S_P_FINAL  = 4'd11,
                     S_DONE     = 4'd12;

    reg [3:0]  state = S_IDLE;
    reg [3:0]  rnd   = 4'd0;
    reg [63:0] x0, x1, x2, x3, x4;
    reg [63:0] k0, k1;
    reg [3:0]  na, np, ad_cnt, pt_cnt;

    wire [7:0]   rc = {4'd15 - rnd, rnd};   // 0xF0, 0xE1, ..., 0x4B
    wire [63:0]  r0, r1, r2, r3, r4;
    wire [319:0] sbox_y;
    (* KEEP_HIERARCHY = "yes", DONT_TOUCH = "true" *)
    ascon_round u_round (
        .x0_i(x0), .x1_i(x1), .x2_i(x2), .x3_i(x3), .x4_i(x4),
        .rc  (rc),
        .x0_o(r0), .x1_o(r1), .x2_o(r2), .x3_o(r3), .x4_o(r4),
        .sbox_o(sbox_y)
    );

    wire in_round = (state == S_P_INIT) || (state == S_P_AD) || (state == S_P_ADPAD) ||
                    (state == S_P_PT)   || (state == S_P_FINAL);

    assign din_ready = (state == S_AD_WAIT) || (state == S_PT_WAIT);
    assign state_o   = {x4, x3, x2, x1, x0};
    assign busy      = (state != S_IDLE);

    // error detection
    wire chk_err;
    generate
        if (CHECK == 1) begin : g_sbox_sig
            wire [63:0] g_pred;
            (* KEEP_HIERARCHY = "yes", DONT_TOUCH = "true" *)
            ascon_sbox_par u_pred (.x0(x0), .x1(x1), .x2(x2), .x3(x3), .x4(x4), .rc(rc), .g(g_pred));
            wire [63:0] g_act = sbox_y[63:0] ^ sbox_y[127:64] ^ sbox_y[191:128] ^
                                sbox_y[255:192] ^ sbox_y[319:256];
            reg err_q = 1'b0;
            always @(posedge clk)
                err_q <= in_round && (|(g_pred ^ g_act));
            assign chk_err = err_q;
        end else if (CHECK == 5 || CHECK == 6) begin : g_sbox_sig_k
            localparam integer M = (CHECK == 5) ? 2 : 3;
            wire [64*M-1:0] s_pred, s_act;
            (* KEEP_HIERARCHY = "yes", DONT_TOUCH = "true" *)
            ascon_sbox_sig #(.MODE(M)) u_pred (.x0(x0), .x1(x1), .x2(x2), .x3(x3), .x4(x4), .rc(rc), .sig(s_pred));
            ascon_sig_of #(.MODE(M)) u_act (.d0(sbox_y[63:0]), .d1(sbox_y[127:64]), .d2(sbox_y[191:128]),
                                            .d3(sbox_y[255:192]), .d4(sbox_y[319:256]), .sig(s_act));
            reg err_q = 1'b0;
            always @(posedge clk)
                err_q <= in_round && (|(s_pred ^ s_act));
            assign chk_err = err_q;
        // same CRC-3 code, signatures registered and compared one cycle later
        end else if (CHECK == 7) begin : g_sbox_sig_reg
            wire [191:0] s_pred, s_act;
            (* KEEP_HIERARCHY = "yes", DONT_TOUCH = "true" *)
            ascon_sbox_sig #(.MODE(3)) u_pred (.x0(x0), .x1(x1), .x2(x2), .x3(x3), .x4(x4), .rc(rc), .sig(s_pred));
            ascon_sig_of #(.MODE(3)) u_act (.d0(sbox_y[63:0]), .d1(sbox_y[127:64]), .d2(sbox_y[191:128]),
                                            .d3(sbox_y[255:192]), .d4(sbox_y[319:256]), .sig(s_act));
            reg [191:0] sp_q = 192'd0, sa_q = 192'd0;
            reg         v_q = 1'b0, err_q = 1'b0;
            always @(posedge clk) begin
                sp_q  <= s_pred;
                sa_q  <= s_act;
                v_q   <= in_round && !rst;
                err_q <= v_q && (|(sp_q ^ sa_q));
            end
            assign chk_err = err_q;
        // predicted parity of the next state, compared with the stored state one cycle later
        end else if (CHECK == 2) begin : g_round_par
            wire [63:0] g_pred;
            (* KEEP_HIERARCHY = "yes", DONT_TOUCH = "true" *)
            ascon_sbox_par u_pred (.x0(x0), .x1(x1), .x2(x2), .x3(x3), .x4(x4), .rc(rc), .g(g_pred));
            reg pred_q  = 1'b0;
            reg valid_q = 1'b0;
            reg err_q   = 1'b0;
            wire act_par = ^{x0, x1, x2, x3, x4};
            always @(posedge clk) begin
                pred_q  <= ^g_pred;
                valid_q <= in_round && !rst;
                err_q   <= valid_q && (act_par != pred_q);
            end
            assign chk_err = err_q;
        end else if (CHECK == 3) begin : g_group_par
            wire gp_err;
            (* KEEP_HIERARCHY = "yes" *)
            ascon_gpar_check u_gchk (.clk(clk), .rst(rst), .in_round(in_round),
                                     .x0(x0), .x1(x1), .x2(x2), .x3(x3), .x4(x4), .rc(rc),
                                     .err(gp_err));
            assign chk_err = gp_err;
        end else begin : g_none
            assign chk_err = 1'b0;
        end
    endgenerate

    reg alarm_r = 1'b0;
    assign alarm = alarm_r | chk_err;

    always @(posedge clk) begin
        if (rst) begin
            state      <= S_IDLE;
            dout_valid <= 1'b0;
            done       <= 1'b0;
            alarm_r    <= 1'b0;
        end else begin
            dout_valid <= 1'b0;
            done       <= 1'b0;
            if (chk_err) alarm_r <= 1'b1;

            case (state)
                S_IDLE: begin
                    if (start) begin
                        x0 <= IV;
                        x1 <= key[63:0];
                        x2 <= key[127:64];
                        x3 <= nonce[63:0];
                        x4 <= nonce[127:64];
                        k0 <= key[63:0];
                        k1 <= key[127:64];
                        na <= ad_blocks;
                        np <= pt_blocks;
                        ad_cnt  <= 4'd0;
                        pt_cnt  <= 4'd0;
                        rnd     <= 4'd0;
                        alarm_r <= 1'b0;
                        state   <= S_P_INIT;
                    end
                end

                S_P_INIT, S_P_AD, S_P_ADPAD, S_P_PT, S_P_FINAL: begin
                    x0 <= r0;  x1 <= r1;  x2 <= r2;  x3 <= r3;  x4 <= r4;
                    if (rnd == 4'd11) begin
                        case (state)
                            S_P_INIT:  state <= S_INIT_KEY;
                            S_P_AD:    state <= (ad_cnt == na) ? S_AD_PAD : S_AD_WAIT;
                            S_P_ADPAD: state <= S_DOMSEP;
                            S_P_PT:    state <= (pt_cnt == np) ? S_PT_PAD : S_PT_WAIT;
                            default:   state <= S_DONE;
                        endcase
                    end else begin
                        rnd <= rnd + 4'd1;
                    end
                end

                S_INIT_KEY: begin
                    x3 <= x3 ^ k0;
                    x4 <= x4 ^ k1;
                    state <= (na != 4'd0) ? S_AD_WAIT : S_DOMSEP;
                end

                S_AD_WAIT: begin
                    if (din_valid) begin
                        x0 <= x0 ^ din[63:0];
                        x1 <= x1 ^ din[127:64];
                        ad_cnt <= ad_cnt + 4'd1;
                        rnd    <= 4'd4;
                        state  <= S_P_AD;
                    end
                end

                S_AD_PAD: begin   // padding block 0x01
                    x0    <= x0 ^ 64'h0000000000000001;
                    rnd   <= 4'd4;
                    state <= S_P_ADPAD;
                end

                S_DOMSEP: begin   // domain separation
                    x4    <= x4 ^ 64'h8000000000000000;
                    state <= (np != 4'd0) ? S_PT_WAIT : S_PT_PAD;
                end

                S_PT_WAIT: begin
                    if (din_valid) begin
                        x0 <= x0 ^ din[63:0];
                        x1 <= x1 ^ din[127:64];
                        dout       <= {x1 ^ din[127:64], x0 ^ din[63:0]};
                        dout_valid <= 1'b1;
                        pt_cnt <= pt_cnt + 4'd1;
                        rnd    <= 4'd4;
                        state  <= S_P_PT;
                    end
                end

                S_PT_PAD: begin   // last padding block and finalization key
                    x0    <= x0 ^ 64'h0000000000000001;
                    x2    <= x2 ^ k0;
                    x3    <= x3 ^ k1;
                    rnd   <= 4'd0;
                    state <= S_P_FINAL;
                end

                S_DONE: begin
                    tag   <= {x4 ^ k1, x3 ^ k0};
                    done  <= 1'b1;
                    state <= S_IDLE;
                end

                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
