// UART receiver, 8N1. valid pulses for one clock with a new byte.
module uart_rx #(
    parameter integer CLKS_PER_BIT = 104
) (
    input  wire       clk,
    input  wire       rx,
    output reg  [7:0] data  = 8'd0,
    output reg        valid = 1'b0
);
    // synchronizer
    (* ASYNC_REG = "TRUE" *) reg rx_meta = 1'b1;
    (* ASYNC_REG = "TRUE" *) reg rx_sync = 1'b1;
    always @(posedge clk) begin
        rx_meta <= rx;
        rx_sync <= rx_meta;
    end

    localparam [1:0] S_IDLE = 2'd0, S_START = 2'd1, S_DATA = 2'd2, S_STOP = 2'd3;
    reg [1:0]  state   = S_IDLE;
    reg [15:0] cnt     = 16'd0;
    reg [2:0]  bit_idx = 3'd0;
    reg [7:0]  shift   = 8'd0;

    always @(posedge clk) begin
        valid <= 1'b0;
        case (state)
            S_IDLE: begin
                cnt     <= 16'd0;
                bit_idx <= 3'd0;
                if (rx_sync == 1'b0)
                    state <= S_START;
            end
            S_START: begin
                if (cnt == (CLKS_PER_BIT-1)/2) begin
                    cnt   <= 16'd0;
                    state <= (rx_sync == 1'b0) ? S_DATA : S_IDLE;
                end else
                    cnt <= cnt + 16'd1;
            end
            S_DATA: begin
                if (cnt == CLKS_PER_BIT-1) begin
                    cnt            <= 16'd0;
                    shift[bit_idx] <= rx_sync;
                    if (bit_idx == 3'd7)
                        state <= S_STOP;
                    else
                        bit_idx <= bit_idx + 3'd1;
                end else
                    cnt <= cnt + 16'd1;
            end
            S_STOP: begin
                if (cnt == CLKS_PER_BIT-1) begin
                    cnt   <= 16'd0;
                    state <= S_IDLE;
                    if (rx_sync == 1'b1) begin
                        data  <= shift;
                        valid <= 1'b1;
                    end
                end else
                    cnt <= cnt + 16'd1;
            end
            default: state <= S_IDLE;
        endcase
    end
endmodule
