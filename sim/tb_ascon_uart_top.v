// Board-level test over the UART: version, KAT Count=545 and Count=1057.
// Run about 2.5 ms of simulated time.
`timescale 1ns / 1ps
module tb_ascon_uart_top;
    reg  clk = 1'b0;
    always #41.667 clk = ~clk;

    reg  pc_tx = 1'b1;
    wire pc_rx;
    wire [1:0] led;

    ascon_uart_top dut (.clk(clk), .uart_txd_in(pc_tx), .uart_rxd_out(pc_rx), .led(led));

    localparam real PC_BIT   = 1.0e9 / 921600.0;
    localparam real FPGA_BIT = 109 * 10.0;

    integer errors = 0;

    task send_byte(input [7:0] b);
        integer i;
        begin
            pc_tx = 1'b0;  #(PC_BIT);
            for (i = 0; i < 8; i = i + 1) begin pc_tx = b[i]; #(PC_BIT); end
            pc_tx = 1'b1;  #(PC_BIT);
        end
    endtask

    task recv_byte(output [7:0] b);
        integer i;
        begin
            @(negedge pc_rx);
            #(FPGA_BIT * 1.5);
            for (i = 0; i < 8; i = i + 1) begin b[i] = pc_rx; #(FPGA_BIT); end
        end
    endtask

    task send_key_nonce;
        integer i;
        begin
            for (i = 0; i < 16; i = i + 1) send_byte(i);
            for (i = 0; i < 16; i = i + 1) send_byte(8'h10 + i);
        end
    endtask

    task recv_and_check(input integer n, input [383:0] expected, input [8*12:1] name);
        integer i;
        reg [7:0] b;
        begin
            for (i = 0; i < n; i = i + 1) begin
                recv_byte(b);
                if (b !== expected[383 - 8*i -: 8]) begin
                    $display("%0s: byte %0d got %h expected %h", name, i, b, expected[383 - 8*i -: 8]);
                    errors = errors + 1;
                end
            end
        end
    endtask

    task recv_status(input [8*12:1] name);
        reg [7:0] s, c0, c1;
        begin
            recv_byte(s); recv_byte(c0); recv_byte(c1);
            $display("%0s: status=%h, core cycles=%0d", name, s, {c1, c0});
            if (s !== 8'h00) errors = errors + 1;
        end
    endtask

    integer i;
    initial begin
        wait (dut.locked === 1'b1);
        #2000;

        fork
            send_byte(8'h56);
            recv_and_check(4, {"ASC1", 352'd0}, "version");
        join
        $display("version check done");

        fork
            begin
                send_byte(8'h45); send_byte(8'h11);
                send_key_nonce;
                for (i = 0; i < 16; i = i + 1) send_byte(8'h30 + i);
                for (i = 0; i < 16; i = i + 1) send_byte(8'h20 + i);
            end
            begin
                recv_and_check(32,
                    {256'h6373EBB28BE97C9BAC090CF399C13EF13ABFC0D209E8F4844C90814D13F32C59, 128'd0},
                    "KAT545");
                recv_status("KAT545");
            end
        join

        fork
            begin
                send_byte(8'h45); send_byte(8'h02);
                send_key_nonce;
                for (i = 0; i < 32; i = i + 1) send_byte(8'h20 + i);
            end
            begin
                recv_and_check(48,
                    384'hE8C3DEEE246CC5EAE3E872313897A2BB6089AA3E15E80307970F2D1F006654C2AAA5FA172CB9F07D07463CEFC7440BC1,
                    "KAT1057");
                recv_status("KAT1057");
            end
        join

        if (errors == 0) $display("ALL BOARD-LEVEL TESTS PASSED");
        else             $display("%0d ERRORS", errors);
        $finish;
    end
endmodule
