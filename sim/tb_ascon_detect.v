// Fault injection in simulation: no checker vs. S-box parity vs. global parity.
// Faults are forced into the state register and into an S-box output.
`timescale 1ns / 1ps
module tb_ascon_detect;
    reg clk = 1'b0;
    always #5 clk = ~clk;

    reg          rst = 1'b1, start = 1'b0;
    reg  [127:0] key, nonce;
    reg  [127:0] blocks [0:1];
    integer      in_idx = 0;
    wire [127:0] din       = blocks[in_idx];
    wire         din_valid = (in_idx < 2);

    wire         rdy0, rdy1, rdy2, dv0, dv1, dv2, done0, done1, done2, b0, b1, b2, al0, al1, al2;
    wire [127:0] dout0, dout1, dout2, tag0, tag1, tag2;

    ascon_aead128_enc #(.CHECK(0)) core0 (.clk(clk), .rst(rst), .start(start), .key(key), .nonce(nonce),
        .ad_blocks(4'd1), .pt_blocks(4'd1), .din(din), .din_valid(din_valid), .din_ready(rdy0),
        .dout(dout0), .dout_valid(dv0), .tag(tag0), .done(done0), .busy(b0), .alarm(al0));
    ascon_aead128_enc #(.CHECK(1)) core1 (.clk(clk), .rst(rst), .start(start), .key(key), .nonce(nonce),
        .ad_blocks(4'd1), .pt_blocks(4'd1), .din(din), .din_valid(din_valid), .din_ready(rdy1),
        .dout(dout1), .dout_valid(dv1), .tag(tag1), .done(done1), .busy(b1), .alarm(al1));
    ascon_aead128_enc #(.CHECK(2)) core2 (.clk(clk), .rst(rst), .start(start), .key(key), .nonce(nonce),
        .ad_blocks(4'd1), .pt_blocks(4'd1), .din(din), .din_valid(din_valid), .din_ready(rdy2),
        .dout(dout2), .dout_valid(dv2), .tag(tag2), .done(done2), .busy(b2), .alarm(al2));

    always @(posedge clk)
        if (rdy0 && din_valid) in_idx <= in_idx + 1;

    localparam [127:0] GOOD_TAG = 128'h592cf3134d81904c84f4e809d2c0bf3a;

    integer errors = 0;
    integer k;

    task run_case(input [8*24:1] name, input integer kind, input [2:0] exp_alarm);
        reg [2:0] got;
        begin
            @(negedge clk);
            in_idx = 0;
            start  = 1'b1;
            @(negedge clk);
            start  = 1'b0;
            repeat (5) @(negedge clk);

            if (kind == 1) begin
                core0.x2 = core0.x2 ^ 64'h20;
                core1.x2 = core1.x2 ^ 64'h20;
                core2.x2 = core2.x2 ^ 64'h20;
            end else if (kind == 2) begin
                core0.x0 = core0.x0 ^ 64'h8;  core0.x3 = core0.x3 ^ (64'h1 << 40);
                core1.x0 = core1.x0 ^ 64'h8;  core1.x3 = core1.x3 ^ (64'h1 << 40);
                core2.x0 = core2.x0 ^ 64'h8;  core2.x3 = core2.x3 ^ (64'h1 << 40);
            end else if (kind == 3) begin
                force core0.u_round.d1[0] = ~(core0.u_round.c1[0] ^ core0.u_round.c0[0]);
                force core1.u_round.d1[0] = ~(core1.u_round.c1[0] ^ core1.u_round.c0[0]);
                force core2.u_round.d1[0] = ~(core2.u_round.c1[0] ^ core2.u_round.c0[0]);
                @(negedge clk);
                release core0.u_round.d1[0];
                release core1.u_round.d1[0];
                release core2.u_round.d1[0];
            end

            wait (done0 === 1'b1);
            #1;
            got = {al2, al1, al0};
            $display("%0s | alarms core0/1/2 = %b %b %b | tag %s", name, al0, al1, al2,
                     (tag0 === GOOD_TAG) ? "correct" : "WRONG (fault reached the output)");
            if (got !== exp_alarm) begin
                $display("   ^ UNEXPECTED: expected alarms %b %b %b", exp_alarm[0], exp_alarm[1], exp_alarm[2]);
                errors = errors + 1;
            end
            if (kind == 0 && (tag0 !== GOOD_TAG || tag1 !== GOOD_TAG || tag2 !== GOOD_TAG)) begin
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

        run_case("A) no fault             ", 0, 3'b000);
        run_case("B) 1-bit register fault ", 1, 3'b100);
        run_case("C) 2-bit register fault ", 2, 3'b000);
        run_case("D) S-box output fault   ", 3, 3'b110);

        if (errors == 0) $display("DETECTOR BEHAVIOUR AS EXPECTED");
        else             $display("%0d UNEXPECTED RESULTS", errors);
        $finish;
    end
endmodule
