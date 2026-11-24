// Glitch clock, snapshot and TDC commands in simulation (no gate delays, so
// no faults): checks that exactly one short cycle is produced and that no clock
// pulse is narrower than 0.8 ns.
`timescale 1ns / 1ps
module tb_glitch_top;
    reg  clk = 1'b0;
    always #41.667 clk = ~clk;

    reg  pc_tx = 1'b1;
    wire pc_rx;
    wire [1:0] led;

    ascon_uart_top #(.VARIANT(0)) dut (.clk(clk), .uart_txd_in(pc_tx), .uart_rxd_out(pc_rx), .led(led));

    localparam real PC_BIT   = 1.0e9 / 921600.0;
    localparam real FPGA_BIT = 109 * 10.0;

    integer errors = 0;

    real    last_rise = 0.0, last_fall = 0.0;
    real    period = 0.0, width = 0.0;
    real    min_period = 100.0, min_high = 100.0, min_low = 100.0;
    integer short_cycles = 0;
    reg     watching = 1'b0;
    always @(posedge dut.gclk) begin
        if (watching) begin
            period = $realtime - last_rise;
            width  = $realtime - last_fall;
            if (period < min_period) min_period = period;
            if (width  < min_low)    min_low    = width;
            if (period < 9.0) short_cycles = short_cycles + 1;
        end
        last_rise = $realtime;
    end
    always @(negedge dut.gclk) begin
        if (watching) begin
            width = $realtime - last_rise;
            if (width < min_high) min_high = width;
        end
        last_fall = $realtime;
    end

    task send_byte(input [7:0] b);
        integer k;
        begin
            pc_tx = 1'b0;  #(PC_BIT);
            for (k = 0; k < 8; k = k + 1) begin pc_tx = b[k]; #(PC_BIT); end
            pc_tx = 1'b1;  #(PC_BIT);
        end
    endtask

    task recv_byte(output [7:0] b);
        integer k;
        begin
            @(negedge pc_rx);
            #(FPGA_BIT * 1.5);
            for (k = 0; k < 8; k = k + 1) begin b[k] = pc_rx; #(FPGA_BIT); end
        end
    endtask

    task expect_byte(input [7:0] e, input [8*16:1] what);
        reg [7:0] b;
        begin
            recv_byte(b);
            if (b !== e) begin
                $display("%0s: got %h expected %h", what, b, e);
                errors = errors + 1;
            end
        end
    endtask

    localparam [127:0] KAT1_TAG = 128'h4F9C278211BEC9316BF68F46EE8B2EC6;
    localparam [319:0] SNAP_EXP = 320'hd99426a9b964e9828cdf1337d0c7511c54682c30877adba54ba23063593e3d9a9ca793f1a5538c9b;

    integer i, j, reached;
    reg [7:0]   st, c0, c1, b, pol;
    reg [383:0] tdc;
    initial begin
        wait (dut.locked === 1'b1);
        #3000;

        fork
            send_byte(8'h56);
            begin
                expect_byte("A", "version"); expect_byte("S", "version");
                expect_byte("C", "version"); expect_byte("1", "version");
            end
        join
        $display("1) version OK");

        fork
            begin send_byte(8'h50); send_byte(8'd150); send_byte(8'd0); end
            expect_byte(8'h4B, "phase ack");
        join
        $display("2) phase set, ps_current = %0d", dut.ps_current);
        if (dut.ps_current !== 9'd150) errors = errors + 1;

        fork
            begin
                send_byte(8'h47); send_byte(8'd3); send_byte(8'd0);
                send_byte(8'd5);  send_byte(8'd0); send_byte(8'd3);
            end
            expect_byte(8'h4B, "glitch ack");
        join
        $display("3) glitch armed");

        watching = 1'b1;
        fork
            begin
                send_byte(8'h45); send_byte(8'h00);
                for (i = 0; i < 16; i = i + 1) send_byte(i);
                for (i = 0; i < 16; i = i + 1) send_byte(8'h10 + i);
            end
            begin
                for (j = 0; j < 16; j = j + 1)
                    expect_byte(KAT1_TAG[127 - 8*j -: 8], "tag");
                recv_byte(st); recv_byte(c0); recv_byte(c1);
            end
        join
        watching = 1'b0;
        $display("4) encryption: status=%h cycles=%0d | short clock cycles = %0d, shortest = %0.3f ns",
                 st, {c1, c0}, short_cycles, min_period);
        $display("   narrowest clock pulses: high %0.3f ns, low %0.3f ns", min_high, min_low);
        if (st !== 8'h00) errors = errors + 1;
        if (short_cycles != 1) begin
            $display("   ^ expected exactly 1 short cycle");
            errors = errors + 1;
        end
        if (min_high < 0.8 || min_low < 0.8) begin
            $display("   ^ a clock pulse is narrower than 0.8 ns");
            errors = errors + 1;
        end

        fork
            send_byte(8'h53);
            for (j = 0; j < 40; j = j + 1)
                expect_byte(SNAP_EXP[319 - 8*j -: 8], "snapshot");
        join
        $display("5) snapshot checked");

        fork
            send_byte(8'h54);
            begin
                for (j = 0; j < 48; j = j + 1) begin
                    recv_byte(b);
                    tdc[8*j +: 8] = b;
                end
                recv_byte(pol);
            end
        join
        reached = 0;
        for (j = 0; j < 384; j = j + 1)
            if (tdc[j] == pol[0]) reached = reached + 1;
        $display("6) ruler: polarity %0d, %0d of 384 taps reached (simulation has no delays: expect 384)",
                 pol[0], reached);
        if (reached != 384 || pol[7:1] != 7'd0) errors = errors + 1;

        if (errors == 0) $display("ALL GLITCH-CONTROL TESTS PASSED");
        else             $display("%0d ERRORS", errors);
        $finish;
    end
endmodule
