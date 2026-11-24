// Carry-chain TDC that measures the length of the glitched cycle.
// launch toggles every cycle; taps shows how far the edge got along 4*N4 carry
// elements before the next clock edge (about 17.7 ps per tap on our board).
// The chain is a false path in glitch_timing.xdc.
module glitch_tdc #(
    parameter integer N4 = 96
) (
    input  wire            clk,
    output wire            launch,
    output wire [4*N4-1:0] taps
);
    (* DONT_TOUCH = "true" *) reg launch_q = 1'b0;
    always @(posedge clk)
        launch_q <= ~launch_q;
    assign launch = launch_q;

    wire [4*N4-1:0] co;
    genvar j;
    generate
        for (j = 0; j < N4; j = j + 1) begin : g_chain
            if (j == 0) begin : g_first
                (* DONT_TOUCH = "true" *)
                CARRY4 u_c4 (.CO(co[3:0]), .O(), .CI(1'b0), .CYINIT(launch_q),
                             .DI(4'b0000), .S(4'b1111));
            end else begin : g_next
                (* DONT_TOUCH = "true" *)
                CARRY4 u_c4 (.CO(co[4*j+3:4*j]), .O(), .CI(co[4*j-1]), .CYINIT(1'b0),
                             .DI(4'b0000), .S(4'b1111));
            end
        end
    endgenerate

    (* DONT_TOUCH = "true" *) reg [4*N4-1:0] taps_q = {4*N4{1'b0}};
    always @(posedge clk)
        taps_q <= co;
    assign taps = taps_q;
endmodule
