// Separate copies of the S-box layer for the checkers. The instances are marked
// KEEP_HIERARCHY/DONT_TOUCH so synthesis cannot share logic with the datapath.
// g[i] = parity of the S-box output of column i
module ascon_sbox_par (
    input  wire [63:0] x0, x1, x2, x3, x4,
    input  wire [7:0]  rc,
    output wire [63:0] g
);
    wire [63:0] a2 = x2 ^ {56'd0, rc};

    wire [63:0] b0 = x0 ^ x4;
    wire [63:0] b1 = x1;
    wire [63:0] b2 = a2 ^ x1;
    wire [63:0] b3 = x3;
    wire [63:0] b4 = x4 ^ x3;

    wire [63:0] c0 = b0 ^ (~b1 & b2);
    wire [63:0] c1 = b1 ^ (~b2 & b3);
    wire [63:0] c2 = b2 ^ (~b3 & b4);
    wire [63:0] c3 = b3 ^ (~b4 & b0);
    wire [63:0] c4 = b4 ^ (~b0 & b1);

    wire [63:0] d0 = c0 ^ c4;
    wire [63:0] d1 = c1 ^ c0;
    wire [63:0] d2 = ~c2;
    wire [63:0] d3 = c3 ^ c2;
    wire [63:0] d4 = c4;

    assign g = d0 ^ d1 ^ d2 ^ d3 ^ d4;
endmodule

// S-box output signatures, after Kaur et al. (TCAS-II, 2022):
//   MODE 1: y0^y1^y2^y3^y4
//   MODE 2: y0^y2^y4, y1^y3
//   MODE 3: CRC-3, y(x) mod x^3+x+1: y0^y3, y1^y3^y4, y2^y4
module ascon_sig_of #(
    parameter integer MODE = 2
) (
    input  wire [63:0]        d0, d1, d2, d3, d4,
    output wire [64*MODE-1:0] sig
);
    generate
        if (MODE == 1) begin : g_m1
            assign sig = d0 ^ d1 ^ d2 ^ d3 ^ d4;
        end else if (MODE == 2) begin : g_m2
            assign sig = {d1 ^ d3, d0 ^ d2 ^ d4};
        end else begin : g_m3
            assign sig = {d2 ^ d4, d1 ^ d3 ^ d4, d0 ^ d3};
        end
    endgenerate
endmodule

// predicted signature (own S-box copy + ascon_sig_of)
module ascon_sbox_sig #(
    parameter integer MODE = 2
) (
    input  wire [63:0]        x0, x1, x2, x3, x4,
    input  wire [7:0]         rc,
    output wire [64*MODE-1:0] sig
);
    wire [63:0] a2 = x2 ^ {56'd0, rc};
    wire [63:0] b0 = x0 ^ x4, b1 = x1, b2 = a2 ^ x1, b3 = x3, b4 = x4 ^ x3;
    wire [63:0] c0 = b0 ^ (~b1 & b2), c1 = b1 ^ (~b2 & b3), c2 = b2 ^ (~b3 & b4);
    wire [63:0] c3 = b3 ^ (~b4 & b0), c4 = b4 ^ (~b0 & b1);
    ascon_sig_of #(.MODE(MODE)) u_sig (.d0(c0 ^ c4), .d1(c1 ^ c0), .d2(~c2), .d3(c3 ^ c2), .d4(c4),
                                       .sig(sig));
endmodule
