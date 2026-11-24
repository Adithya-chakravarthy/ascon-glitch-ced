// UART command interface for the core and the glitch experiments.
//
//   'V'                        -> "ASC1"
//   'Q'                        -> VARIANT (1 byte)
//   'E' hdr key nonce AD PT    -> CT, tag, status, cycles (2 bytes)
//       hdr = (na << 4) | np, na, np <= 4; status bit 0 = alarm, bit 1 = timeout
//   'P' lo hi                  -> set fine phase (29.76 ps steps), 'K' when reached
//   'G' t_lo t_hi s_lo s_hi en -> glitch init round t, snapshot at cycle s, 'K'
//                                 en bit 0: enable, bit 1: early request timing
//   'S'                        -> 40-byte state snapshot (x0..x4, little-endian)
//   'T'                        -> 48 TDC tap bytes + polarity byte
//
// Inside 'E', cycle 0 is the start pulse and init round j runs in cycle j+1.
// VARIANT 4 adds a second core in lockstep (duplication).
module ascon_uart_ctrl #(
    parameter integer CLKS_PER_BIT = 109,   // 100 MHz / 921600 baud
    parameter integer VARIANT      = 0
) (
    input  wire       clk,
    input  wire       rst,
    input  wire       uart_rx_pin,
    output wire       uart_tx_pin,
    output reg        activity = 1'b0,
    output reg        glitch_arm   = 1'b0,
    output reg        glitch_early = 1'b0,
    output reg  [8:0] ps_target  = 9'd0,
    input  wire       ps_idle
);
    localparam [3:0] C_IDLE   = 4'd0,
                     C_HDR    = 4'd1,
                     C_KEY    = 4'd2,
                     C_NONCE  = 4'd3,
                     C_DATA   = 4'd4,
                     C_START  = 4'd5,
                     C_RUN    = 4'd6,
                     C_TXLOAD = 4'd7,
                     C_TXSEND = 4'd8,
                     C_ARGS   = 4'd9,
                     C_PSWAIT = 4'd10;
    reg [3:0] state = C_IDLE;

    wire [7:0] rx_data;
    wire       rx_valid;
    uart_rx #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_rx (
        .clk(clk), .rx(uart_rx_pin), .data(rx_data), .valid(rx_valid));

    reg  [7:0] tx_data  = 8'd0;
    reg        tx_start = 1'b0;
    wire       tx_busy;
    uart_tx #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_tx (
        .clk(clk), .data(tx_data), .start(tx_start), .tx(uart_tx_pin), .busy(tx_busy));

    reg  [127:0] key = 128'd0, nonce = 128'd0;
    reg  [3:0]   na = 4'd0, np = 4'd0;
    reg  [127:0] inbuf [0:7];
    reg  [127:0] ctbuf [0:3];
    reg  [3:0]   in_idx = 4'd0, ct_idx = 4'd0;
    reg          core_start = 1'b0;
    reg          core_abort = 1'b0;
    wire         core_rst   = rst | core_abort;

    wire [127:0] din       = inbuf[in_idx[2:0]];
    wire         din_valid = (state == C_RUN) && (in_idx < (na + np));
    wire         din_ready, dout_valid, core_done, core_busy, core_alarm;
    wire [127:0] dout, tag;
    wire [319:0] core_state;

    // VARIANT -> CHECK of the core (4 = duplication, handled below)
    localparam integer CHECK = (VARIANT == 1) ? 1 : (VARIANT == 2) ? 2 : (VARIANT == 3) ? 3 :
                               (VARIANT == 5) ? 5 : (VARIANT == 6) ? 6 : (VARIANT == 7) ? 7 : 0;
    localparam [7:0]   VARIANT_BYTE = VARIANT;

    (* KEEP_HIERARCHY = "yes", DONT_TOUCH = "true" *)
    ascon_aead128_enc #(.CHECK(CHECK)) u_core (
        .clk(clk), .rst(core_rst), .start(core_start),
        .key(key), .nonce(nonce), .ad_blocks(na), .pt_blocks(np),
        .din(din), .din_valid(din_valid), .din_ready(din_ready),
        .dout(dout), .dout_valid(dout_valid),
        .tag(tag), .done(core_done), .busy(core_busy), .alarm(core_alarm),
        .state_o(core_state)
    );

    // duplication: second core, outputs compared
    wire dmr_alarm;
    generate
        if (VARIANT == 4) begin : g_dmr
            wire         rdy_b, dv_b, done_b, busy_b, alarm_b;
            wire [127:0] dout_b, tag_b;
            wire [319:0] state_b;
            (* KEEP_HIERARCHY = "yes", DONT_TOUCH = "true" *)
            ascon_aead128_enc #(.CHECK(0)) u_core_b (
                .clk(clk), .rst(core_rst), .start(core_start),
                .key(key), .nonce(nonce), .ad_blocks(na), .pt_blocks(np),
                .din(din), .din_valid(din_valid), .din_ready(rdy_b),
                .dout(dout_b), .dout_valid(dv_b),
                .tag(tag_b), .done(done_b), .busy(busy_b), .alarm(alarm_b),
                .state_o(state_b)
            );
            wire mism_now = (din_ready != rdy_b) || (dout_valid != dv_b) || (core_done != done_b) ||
                            (dout_valid && (dout != dout_b)) || (core_done && (tag != tag_b));
            reg  mism_q = 1'b0;
            always @(posedge clk) begin
                if (core_start)    mism_q <= 1'b0;
                else if (mism_now) mism_q <= 1'b1;
            end
            assign dmr_alarm = mism_q | mism_now;
        end else begin : g_no_dmr
            assign dmr_alarm = 1'b0;
        end
    endgenerate
    wire alarm_any = core_alarm | dmr_alarm;

    reg [127:0] shreg    = 128'd0;
    reg [3:0]   byte_cnt = 4'd0;
    reg [3:0]   blk_cnt  = 4'd0;
    reg [15:0]  cycles   = 16'd0;
    reg [7:0]   status   = 8'd0;
    reg [4:0]   tx_left  = 5'd0;
    reg [3:0]   tx_blk   = 4'd0;
    reg         tx_last  = 1'b0;
    reg [1:0]   tx_mode  = 2'd0;
    reg [19:0]  silence  = 20'd0;
    reg [7:0]   cmd      = 8'd0;
    reg [2:0]   arg_left = 3'd0;

    // glitch and snapshot settings
    reg [15:0]  g_target = 16'd0;
    reg [15:0]  g_snap   = 16'd0;
    reg         g_en     = 1'b0;
    reg [319:0] snap     = 320'd0;

    // TDC, read with 'T'
    wire         tdc_launch;
    wire [383:0] tdc_taps;
    reg  [383:0] tdc_snap = 384'd0;
    reg          tdc_pol  = 1'b0;
    (* KEEP_HIERARCHY = "yes", DONT_TOUCH = "true" *)
    glitch_tdc #(.N4(96)) u_tdc (.clk(clk), .launch(tdc_launch), .taps(tdc_taps));

    wire [127:0] shreg_in  = {rx_data, shreg[127:8]};
    wire         receiving = (state == C_HDR) || (state == C_KEY) || (state == C_NONCE) ||
                             (state == C_DATA) || (state == C_ARGS);

    always @(posedge clk)
        // arm two cycles before the glitched cycle (round g_target)
        glitch_arm <= g_en && (((state == C_START) && (g_target == 16'd1)) ||
                               ((state == C_RUN) && ((cycles + 16'd2) == g_target)));

    always @(posedge clk) begin
        if (rst) begin
            state      <= C_IDLE;
            tx_start   <= 1'b0;
            core_start <= 1'b0;
            core_abort <= 1'b0;
        end else begin
            tx_start   <= 1'b0;
            core_start <= 1'b0;
            core_abort <= 1'b0;

            // drop a half-received frame after ~10 ms of silence
            if (rx_valid || !receiving)
                silence <= 20'd0;
            else if (silence == 20'hFFFFF)
                state <= C_IDLE;
            else
                silence <= silence + 20'd1;

            case (state)
                C_IDLE: begin
                    if (rx_valid) begin
                        case (rx_data)
                            8'h45: state <= C_HDR;
                            8'h56: begin
                                shreg   <= {96'd0, 8'h31, 8'h43, 8'h53, 8'h41};
                                tx_left <= 5'd4;
                                tx_last <= 1'b1;
                                state   <= C_TXSEND;
                            end
                            8'h51: begin
                                shreg   <= {120'd0, VARIANT_BYTE};
                                tx_left <= 5'd1;
                                tx_last <= 1'b1;
                                state   <= C_TXSEND;
                            end
                            8'h50: begin
                                cmd      <= 8'h50;
                                arg_left <= 3'd2;
                                state    <= C_ARGS;
                            end
                            8'h47: begin
                                cmd      <= 8'h47;
                                arg_left <= 3'd5;
                                state    <= C_ARGS;
                            end
                            8'h53: begin
                                tx_mode <= 2'd1;
                                tx_blk  <= 4'd0;
                                state   <= C_TXLOAD;
                            end
                            8'h54: begin
                                tx_mode <= 2'd2;
                                tx_blk  <= 4'd0;
                                state   <= C_TXLOAD;
                            end
                            default: ;
                        endcase
                    end
                end

                C_ARGS: begin
                    if (rx_valid) begin
                        shreg    <= shreg_in;
                        arg_left <= arg_left - 3'd1;
                        if (arg_left == 3'd1) begin
                            if (cmd == 8'h50) begin
                                ps_target <= shreg_in[120:112];
                                state     <= C_PSWAIT;
                            end else begin
                                g_target <= shreg_in[103:88];
                                g_snap   <= shreg_in[119:104];
                                g_en         <= shreg_in[120];
                                glitch_early <= shreg_in[121];
                                shreg    <= {120'd0, 8'h4B};
                                tx_left  <= 5'd1;
                                tx_last  <= 1'b1;
                                state    <= C_TXSEND;
                            end
                        end
                    end
                end

                C_PSWAIT: begin
                    if (ps_idle) begin
                        shreg   <= {120'd0, 8'h4B};
                        tx_left <= 5'd1;
                        tx_last <= 1'b1;
                        state   <= C_TXSEND;
                    end
                end

                C_HDR: begin
                    if (rx_valid) begin
                        if (rx_data[7:4] > 4'd4 || rx_data[3:0] > 4'd4) begin
                            shreg   <= {120'd0, 8'hEE};
                            tx_left <= 5'd1;
                            tx_last <= 1'b1;
                            state   <= C_TXSEND;
                        end else begin
                            na       <= rx_data[7:4];
                            np       <= rx_data[3:0];
                            byte_cnt <= 4'd0;
                            state    <= C_KEY;
                        end
                    end
                end

                C_KEY: begin
                    if (rx_valid) begin
                        shreg    <= shreg_in;
                        byte_cnt <= byte_cnt + 4'd1;
                        if (byte_cnt == 4'd15) begin
                            key   <= shreg_in;
                            state <= C_NONCE;
                        end
                    end
                end

                C_NONCE: begin
                    if (rx_valid) begin
                        shreg    <= shreg_in;
                        byte_cnt <= byte_cnt + 4'd1;
                        if (byte_cnt == 4'd15) begin
                            nonce   <= shreg_in;
                            blk_cnt <= 4'd0;
                            state   <= ((na + np) == 4'd0) ? C_START : C_DATA;
                        end
                    end
                end

                C_DATA: begin
                    if (rx_valid) begin
                        shreg    <= shreg_in;
                        byte_cnt <= byte_cnt + 4'd1;
                        if (byte_cnt == 4'd15) begin
                            inbuf[blk_cnt[2:0]] <= shreg_in;
                            blk_cnt <= blk_cnt + 4'd1;
                            if ((blk_cnt + 4'd1) == (na + np))
                                state <= C_START;
                        end
                    end
                end

                C_START: begin
                    core_start <= 1'b1;
                    in_idx     <= 4'd0;
                    ct_idx     <= 4'd0;
                    cycles     <= 16'd0;
                    state      <= C_RUN;
                end

                C_RUN: begin
                    cycles <= cycles + 16'd1;
                    if (cycles == g_snap) begin
                        snap     <= core_state;
                        tdc_snap <= tdc_taps;
                        tdc_pol  <= ~tdc_launch;
                    end
                    if (din_ready && din_valid)
                        in_idx <= in_idx + 4'd1;
                    if (dout_valid) begin
                        ctbuf[ct_idx[1:0]] <= dout;
                        ct_idx <= ct_idx + 4'd1;
                    end
                    if (core_done) begin
                        status   <= {7'd0, alarm_any};
                        activity <= ~activity;
                        tx_blk   <= 4'd0;
                        tx_mode  <= 2'd0;
                        state    <= C_TXLOAD;
                    end else if (cycles == 16'd2000) begin
                        status     <= {6'd0, 1'b1, alarm_any};
                        core_abort <= 1'b1;
                        tx_blk     <= 4'd0;
                        tx_mode    <= 2'd0;
                        state      <= C_TXLOAD;
                    end
                end

                C_TXLOAD: begin
                    if (tx_mode == 2'd2) begin
                        if (tx_blk == 4'd0) begin
                            shreg <= tdc_snap[127:0];   tx_left <= 5'd16; tx_last <= 1'b0;
                        end else if (tx_blk == 4'd1) begin
                            shreg <= tdc_snap[255:128]; tx_left <= 5'd16; tx_last <= 1'b0;
                        end else if (tx_blk == 4'd2) begin
                            shreg <= tdc_snap[383:256]; tx_left <= 5'd16; tx_last <= 1'b0;
                        end else begin
                            shreg <= {127'd0, tdc_pol}; tx_left <= 5'd1;  tx_last <= 1'b1;
                        end
                    end else if (tx_mode == 2'd1) begin
                        if (tx_blk == 4'd0) begin
                            shreg <= snap[127:0];    tx_left <= 5'd16; tx_last <= 1'b0;
                        end else if (tx_blk == 4'd1) begin
                            shreg <= snap[255:128];  tx_left <= 5'd16; tx_last <= 1'b0;
                        end else begin
                            shreg <= {64'd0, snap[319:256]}; tx_left <= 5'd8; tx_last <= 1'b1;
                        end
                    end else if (tx_blk < np) begin
                        shreg   <= ctbuf[tx_blk[1:0]];
                        tx_left <= 5'd16;
                        tx_last <= 1'b0;
                    end else if (tx_blk == np) begin
                        shreg   <= tag;
                        tx_left <= 5'd16;
                        tx_last <= 1'b0;
                    end else begin
                        shreg   <= {104'd0, cycles, status};
                        tx_left <= 5'd3;
                        tx_last <= 1'b1;
                    end
                    state <= C_TXSEND;
                end

                C_TXSEND: begin
                    if (tx_left == 5'd0) begin
                        if (tx_last) state <= C_IDLE;
                        else begin
                            tx_blk <= tx_blk + 4'd1;
                            state  <= C_TXLOAD;
                        end
                    end else if (!tx_busy && !tx_start) begin
                        tx_data  <= shreg[7:0];
                        tx_start <= 1'b1;
                        shreg    <= {8'd0, shreg[127:8]};
                        tx_left  <= tx_left - 5'd1;
                    end
                end

                default: state <= C_IDLE;
            endcase
        end
    end
endmodule
