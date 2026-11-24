// UART transmitter, 8N1, LSB first. Pulse start while busy is low.
module uart_tx #(
    parameter integer CLKS_PER_BIT = 104
) (
    input  wire       clk,
    input  wire [7:0] data,
    input  wire       start,
    output reg        tx   = 1'b1,
    output reg        busy = 1'b0
);
    reg [9:0]  frame   = 10'h3FF;
    reg [3:0]  bit_cnt = 4'd0;
    reg [15:0] cnt     = 16'd0;

    always @(posedge clk) begin
        if (!busy) begin
            tx <= 1'b1;
            if (start) begin
                frame   <= {1'b1, data, 1'b0};
                busy    <= 1'b1;
                bit_cnt <= 4'd0;
                cnt     <= 16'd0;
                tx      <= 1'b0;
            end
        end else begin
            if (cnt == CLKS_PER_BIT-1) begin
                cnt <= 16'd0;
                if (bit_cnt == 4'd9) begin
                    busy <= 1'b0;
                    tx   <= 1'b1;
                end else begin
                    bit_cnt <= bit_cnt + 4'd1;
                    frame   <= {1'b1, frame[9:1]};
                    tx      <= frame[1];
                end
            end else
                cnt <= cnt + 16'd1;
        end
    end
endmodule
