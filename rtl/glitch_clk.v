// 100 MHz system clock that can shorten one chosen cycle.
//
// MMCM: 12 MHz x 50 = 600 MHz VCO, three 100 MHz outputs
//   clk0  (CLKOUT1)   normal clock, never glitched
//   clk1  (CLKOUT2)   fine phase shift, provides the early edge at phi
//   clk2n (CLKOUT3B)  fine phase shift, short pulse just before phi
// gclk = (clk0 & ~(gate2 & clk2n)) | (gate1 & clk1), one LUT5 into a BUFG.
// The glitched cycle lasts phi = ps_target * 29.76 ps (1/56 of the VCO period).
module glitch_clk (
    input  wire       clk12,
    output wire       gclk,
    output wire       locked,
    input  wire       arm,
    input  wire       early,   // early request timing, used below 5 ns
    input  wire [8:0] ps_target,
    output reg  [8:0] ps_current = 9'd0,
    output wire       ps_idle
);
    wire clkfb_raw, clkfb, clk0_raw, clk1_raw, clk2n_raw, clk0_b, clk1_b, clk2n_b;
    wire psdone;
    reg  psen = 1'b0, psincdec = 1'b0, ps_busy = 1'b0;

    MMCME2_ADV #(
        .BANDWIDTH           ("OPTIMIZED"),
        .COMPENSATION        ("ZHOLD"),
        .CLKIN1_PERIOD       (83.333),
        .DIVCLK_DIVIDE       (1),
        .CLKFBOUT_MULT_F     (50.0),   // 600 MHz VCO
        .CLKFBOUT_PHASE      (0.0),
        .CLKOUT0_DIVIDE_F    (6.0),
        .CLKOUT1_DIVIDE      (6),
        .CLKOUT1_PHASE       (0.0),
        .CLKOUT1_DUTY_CYCLE  (0.5),
        .CLKOUT2_DIVIDE      (6),
        .CLKOUT2_PHASE       (0.0),
        .CLKOUT2_DUTY_CYCLE  (0.833),
        .CLKOUT2_USE_FINE_PS ("TRUE"),
        .CLKOUT3_DIVIDE      (6),
        .CLKOUT3_PHASE       (0.0),
        .CLKOUT3_DUTY_CYCLE  (0.917),
        .CLKOUT3_USE_FINE_PS ("TRUE")
    ) u_mmcm (
        .CLKIN1 (clk12), .CLKIN2(1'b0), .CLKINSEL(1'b1),
        .CLKFBIN(clkfb), .CLKFBOUT(clkfb_raw), .CLKFBOUTB(),
        .CLKOUT0(), .CLKOUT0B(), .CLKOUT1(clk0_raw), .CLKOUT1B(),
        .CLKOUT2(clk1_raw), .CLKOUT2B(), .CLKOUT3(), .CLKOUT3B(clk2n_raw),
        .CLKOUT4(), .CLKOUT5(), .CLKOUT6(),
        .LOCKED (locked), .CLKINSTOPPED(), .CLKFBSTOPPED(),
        .PWRDWN (1'b0), .RST(1'b0),
        .DADDR  (7'd0), .DCLK(1'b0), .DEN(1'b0), .DI(16'd0), .DO(), .DRDY(), .DWE(1'b0),
        .PSCLK  (gclk), .PSEN(psen), .PSINCDEC(psincdec), .PSDONE(psdone)
    );
    BUFG u_bufg_fb (.I(clkfb_raw), .O(clkfb));
    BUFG u_bufg0   (.I(clk0_raw),  .O(clk0_b));
    BUFG u_bufg1   (.I(clk1_raw),  .O(clk1_b));
    BUFG u_bufg2   (.I(clk2n_raw), .O(clk2n_b));

    // requests are retimed onto clk0
    reg kill_c0 = 1'b0, glitch_c0 = 1'b0, glitch_e = 1'b0;
    always @(posedge clk0_b) begin
        kill_c0   <= arm;
        glitch_c0 <= kill_c0;
    end
    always @(negedge clk0_b)
        glitch_e  <= kill_c0;

    // each gate changes only while its clock is low
    reg gate1 = 1'b0, gate2 = 1'b0;
    always @(negedge clk1_b)  gate1 <= early ? glitch_e : glitch_c0;
    always @(negedge clk2n_b) gate2 <= kill_c0;

    // I0 clk0, I1 gate1, I2 clk1, I3 gate2, I4 clk2n
    wire gclk_raw;
    (* DONT_TOUCH = "true" *)
    LUT5 #(.INIT(32'hC0EA_EAEA)) u_glitch_lut (.O(gclk_raw), .I0(clk0_b), .I1(gate1), .I2(clk1_b),
                                               .I3(gate2), .I4(clk2n_b));
    BUFG u_bufg_g (.I(gclk_raw), .O(gclk));

    // fine phase shift, one step per PSEN/PSDONE
    always @(posedge gclk) begin
        psen <= 1'b0;
        if (!ps_busy) begin
            if (locked && (ps_current != ps_target)) begin
                psen     <= 1'b1;
                psincdec <= (ps_target > ps_current);
                ps_busy  <= 1'b1;
            end
        end else if (psdone) begin
            ps_busy    <= 1'b0;
            ps_current <= psincdec ? ps_current + 9'd1 : ps_current - 9'd1;
        end
    end
    assign ps_idle = !ps_busy && (ps_current == ps_target);
endmodule
