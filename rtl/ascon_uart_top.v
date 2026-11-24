// Top level for the Digilent Cmod A7-35T.
// 12 MHz in, 100 MHz system clock from glitch_clk, UART at 921600 baud.
// VARIANT: 0 none, 1/5/6/7 S-box signatures, 2 global parity, 3 group parity,
//          4 duplication.
module ascon_uart_top #(
    parameter integer VARIANT = 0
) (
    input  wire       clk,   // 12 MHz, L17
    input  wire       uart_txd_in,
    output wire       uart_rxd_out,
    output wire [1:0] led
);
    wire       gclk, locked, ps_idle, glitch_arm, glitch_early;
    wire [8:0] ps_target, ps_current;

    glitch_clk u_clk (
        .clk12     (clk),
        .gclk      (gclk),
        .locked    (locked),
        .arm       (glitch_arm),
        .early     (glitch_early),
        .ps_target (ps_target),
        .ps_current(ps_current),
        .ps_idle   (ps_idle)
    );

    // reset until the MMCM is locked
    reg [3:0] rst_sr = 4'hF;
    always @(posedge gclk)
        rst_sr <= {rst_sr[2:0], ~locked};
    wire rst = rst_sr[3];

    reg [26:0] heartbeat = 27'd0;
    always @(posedge gclk)
        heartbeat <= heartbeat + 27'd1;
    assign led[0] = heartbeat[26];   // alive

    wire activity;
    ascon_uart_ctrl #(.CLKS_PER_BIT(109), .VARIANT(VARIANT)) u_ctrl (
        .clk        (gclk),
        .rst        (rst),
        .uart_rx_pin(uart_txd_in),
        .uart_tx_pin(uart_rxd_out),
        .activity   (activity),
        .glitch_arm  (glitch_arm),
        .glitch_early(glitch_early),
        .ps_target  (ps_target),
        .ps_idle    (ps_idle)
    );
    assign led[1] = activity;
endmodule
