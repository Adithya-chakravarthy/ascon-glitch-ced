// Group-parity checker (CHECK = 3) with whatever ascon_gpar.v was generated last.
// Injects 1- and 2-bit state faults (same group / different groups) and an S-box fault.
`timescale 1ns / 1ps
module tb_ascon_gpar;
    reg clk = 1'b0;
    always #5 clk = ~clk;

    reg          rst = 1'b1, start = 1'b0;
    reg  [127:0] key, nonce;
    reg  [127:0] blocks [0:1];
    integer      in_idx = 0;
    wire [127:0] din       = blocks[in_idx];
    wire         din_valid = (in_idx < 2);

    wire         rdy, dv, done, busy, alarm;
    wire [127:0] dout, tag;

    ascon_aead128_enc #(.CHECK(3)) core (.clk(clk), .rst(rst), .start(start), .key(key), .nonce(nonce),
        .ad_blocks(4'd1), .pt_blocks(4'd1), .din(din), .din_valid(din_valid), .din_ready(rdy),
        .dout(dout), .dout_valid(dv), .tag(tag), .done(done), .busy(busy), .alarm(alarm));

    wire [7:0] k;
    wire       has_same, has_diff;
    wire [8:0] same_a, same_b, diff_a, diff_b;
    ascon_gpar_info info (.k(k), .has_same(has_same), .has_diff(has_diff),
                          .same_a(same_a), .same_b(same_b), .diff_a(diff_a), .diff_b(diff_b));

    always @(posedge clk)
        if (rdy && din_valid) in_idx <= in_idx + 1;

    localparam [127:0] GOOD_TAG = 128'h592cf3134d81904c84f4e809d2c0bf3a;

    integer errors = 0;

    task flip(input [8:0] i);
        begin
            case (i / 64)
                0: core.x0 = core.x0 ^ (64'h1 << (i % 64));
                1: core.x1 = core.x1 ^ (64'h1 << (i % 64));
                2: core.x2 = core.x2 ^ (64'h1 << (i % 64));
                3: core.x3 = core.x3 ^ (64'h1 << (i % 64));
                4: core.x4 = core.x4 ^ (64'h1 << (i % 64));
            endcase
        end
    endtask

    task run_case(input [8*34:1] name, input integer kind, input [8:0] i1, input [8:0] i2,
                  input exp_alarm);
        begin
            @(negedge clk);
            in_idx = 0;
            start  = 1'b1;
            @(negedge clk);
            start  = 1'b0;
            repeat (5) @(negedge clk);

            if (kind == 1) begin
                flip(i1);
            end else if (kind == 2) begin
                flip(i1);
                flip(i2);
            end else if (kind == 3) begin
                force core.u_round.d1[0] = ~(core.u_round.c1[0] ^ core.u_round.c0[0]);
                @(negedge clk);
                release core.u_round.d1[0];
            end

            wait (done === 1'b1);
            #1;
            $display("%0s | alarm = %b | tag %s", name, alarm,
                     (tag === GOOD_TAG) ? "correct" : "WRONG (fault reached the output)");
            if (alarm !== exp_alarm) begin
                $display("   ^ UNEXPECTED: expected alarm = %b", exp_alarm);
                errors = errors + 1;
            end
            if (kind == 0 && tag !== GOOD_TAG) begin
                $display("   ^ UNEXPECTED: fault-free tag is wrong");
                errors = errors + 1;
            end
            @(negedge clk);
        end
    endtask

    initial begin
        key   = 128'h0f0e0d0c0b0a09080706050403020100;
        nonce = 128'h1f1e1d1c1b1a19181716151413121110;
        blocks[0] = 128'h3f3e3d3c3b3a39383736353433323130;
        blocks[1] = 128'h2f2e2d2c2b2a29282726252423222120;
        repeat (3) @(posedge clk);
        rst = 1'b0;
        #1;
        $display("Group-parity detector with k = %0d groups", k);

        run_case("A) no fault                       ", 0, 9'd0,   9'd0,   1'b0);
        run_case("B) 1-bit register fault           ", 1, 9'd133, 9'd0,   1'b1);
        if (has_diff)
            run_case("C) 2 bits, different groups       ", 2, diff_a, diff_b, 1'b1);
        if (has_same)
            run_case("D) 2 bits, same group (escapes)   ", 2, same_a, same_b, 1'b0);
        run_case("E) S-box output fault             ", 3, 9'd0,   9'd0,   1'b1);

        if (errors == 0) $display("GROUP-PARITY DETECTOR AS EXPECTED");
        else             $display("%0d UNEXPECTED RESULTS", errors);
        $finish;
    end
endmodule
