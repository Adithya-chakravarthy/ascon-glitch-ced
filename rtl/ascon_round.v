// One round of the Ascon permutation (combinational).
// sbox_o gives the S-box outputs {d4,d3,d2,d1,d0} to the S-box signature checkers.
module ascon_round (
    input  wire [63:0]  x0_i, x1_i, x2_i, x3_i, x4_i,
    input  wire [7:0]   rc,
    output wire [63:0]  x0_o, x1_o, x2_o, x3_o, x4_o,
    output wire [319:0] sbox_o
);
    // constant addition
    wire [63:0] a0 = x0_i;
    wire [63:0] a1 = x1_i;
    wire [63:0] a2 = x2_i ^ {56'd0, rc};
    wire [63:0] a3 = x3_i;
    wire [63:0] a4 = x4_i;

    // S-box layer, bitsliced
    wire [63:0] b0 = a0 ^ a4;
    wire [63:0] b1 = a1;
    wire [63:0] b2 = a2 ^ a1;
    wire [63:0] b3 = a3;
    wire [63:0] b4 = a4 ^ a3;

    wire [63:0] t0 = ~b0 & b1;
    wire [63:0] t1 = ~b1 & b2;
    wire [63:0] t2 = ~b2 & b3;
    wire [63:0] t3 = ~b3 & b4;
    wire [63:0] t4 = ~b4 & b0;

    wire [63:0] c0 = b0 ^ t1;
    wire [63:0] c1 = b1 ^ t2;
    wire [63:0] c2 = b2 ^ t3;
    wire [63:0] c3 = b3 ^ t4;
    wire [63:0] c4 = b4 ^ t0;

    wire [63:0] d0 = c0 ^ c4;
    wire [63:0] d1 = c1 ^ c0;
    wire [63:0] d2 = ~c2;
    wire [63:0] d3 = c3 ^ c2;
    wire [63:0] d4 = c4;

    assign sbox_o = {d4, d3, d2, d1, d0};

    // linear layer: x ^ rotr(x, r1) ^ rotr(x, r2)
    assign x0_o = d0 ^ {d0[18:0], d0[63:19]} ^ {d0[27:0], d0[63:28]};   // 19, 28
    assign x1_o = d1 ^ {d1[60:0], d1[63:61]} ^ {d1[38:0], d1[63:39]};   // 61, 39
    assign x2_o = d2 ^ {d2[0],    d2[63:1]}  ^ {d2[5:0],  d2[63:6]};   // 1, 6
    assign x3_o = d3 ^ {d3[9:0],  d3[63:10]} ^ {d3[16:0], d3[63:17]};   // 10, 17
    assign x4_o = d4 ^ {d4[6:0],  d4[63:7]}  ^ {d4[40:0], d4[63:41]};   // 7, 41
endmodule
