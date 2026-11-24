// Checks ascon_aead128_enc against vectors.txt (scripts/gen_vectors.py).
// The first three vectors are official KATs.
`timescale 1ns / 1ps
module tb_ascon_aead128;
    localparam FALLBACK_PATH = "../sim/vectors.txt";   // edit if your simulator runs elsewhere

    reg clk = 1'b0;
    always #5 clk = ~clk;

    reg          rst   = 1'b1;
    reg          start = 1'b0;
    reg  [127:0] key   = 128'd0;
    reg  [127:0] nonce = 128'd0;
    reg  [3:0]   na    = 4'd0;
    reg  [3:0]   np    = 4'd0;

    reg  [127:0] in_blocks [0:31];
    reg  [127:0] exp_ct    [0:15];
    reg  [127:0] exp_tag;
    integer      in_idx   = 0;
    integer      in_total = 0;
    integer      ct_idx   = 0;
    integer      errors   = 0;

    wire [127:0] din       = in_blocks[in_idx];
    wire         din_valid = (in_idx < in_total);
    wire         din_ready, dout_valid, done, busy;
    wire [127:0] dout, tag;

    ascon_aead128_enc dut (
        .clk(clk), .rst(rst), .start(start),
        .key(key), .nonce(nonce), .ad_blocks(na), .pt_blocks(np),
        .din(din), .din_valid(din_valid), .din_ready(din_ready),
        .dout(dout), .dout_valid(dout_valid),
        .tag(tag), .done(done), .busy(busy)
    );

    always @(posedge clk) begin
        if (din_ready && din_valid)
            in_idx <= in_idx + 1;
        if (dout_valid) begin
            if (dout !== exp_ct[ct_idx]) begin
                $display("  CT block %0d MISMATCH: got %h expected %h", ct_idx, dout, exp_ct[ct_idx]);
                errors = errors + 1;
            end
            ct_idx <= ct_idx + 1;
        end
    end

    integer fd, r, t, j, ntests, na_i, np_i, cycles, test_errors;
    reg [127:0] tmp;

    initial begin
        fd = $fopen("vectors.txt", "r");
        if (fd == 0) fd = $fopen(FALLBACK_PATH, "r");
        if (fd == 0) begin
            $display("ERROR: cannot open vectors.txt (also tried %s)", FALLBACK_PATH);
            $finish;
        end
        r = $fscanf(fd, "%d\n", ntests);

        repeat (3) @(posedge clk);
        rst = 1'b0;

        for (t = 0; t < ntests; t = t + 1) begin
            r = $fscanf(fd, "%d %d\n", na_i, np_i);
            r = $fscanf(fd, "%h\n", tmp);  key   = tmp;
            r = $fscanf(fd, "%h\n", tmp);  nonce = tmp;
            for (j = 0; j < na_i + np_i; j = j + 1) begin
                r = $fscanf(fd, "%h\n", tmp);  in_blocks[j] = tmp;
            end
            for (j = 0; j < np_i; j = j + 1) begin
                r = $fscanf(fd, "%h\n", tmp);  exp_ct[j] = tmp;
            end
            r = $fscanf(fd, "%h\n", tmp);  exp_tag = tmp;

            test_errors = errors;
            @(negedge clk);
            na = na_i;  np = np_i;
            in_idx = 0;  in_total = na_i + np_i;  ct_idx = 0;
            start = 1'b1;
            @(negedge clk);
            start = 1'b0;

            cycles = 0;
            while (done !== 1'b1 && cycles < 2000) begin
                @(posedge clk); #1;
                cycles = cycles + 1;
            end

            if (cycles >= 2000) begin
                $display("Test %0d: TIMEOUT (done never came)", t);
                errors = errors + 1;
            end else begin
                if (tag !== exp_tag) begin
                    $display("Test %0d: TAG MISMATCH got %h expected %h", t, tag, exp_tag);
                    errors = errors + 1;
                end
                if (ct_idx != np_i) begin
                    $display("Test %0d: got %0d ciphertext blocks, expected %0d", t, ct_idx, np_i);
                    errors = errors + 1;
                end
            end
            if (t < 3)
                $display("Official KAT %0d (AD=%0d blocks, PT=%0d blocks): %s  [%0d cycles]",
                         t, na_i, np_i, (errors == test_errors) ? "OK" : "FAIL", cycles);
        end
        $fclose(fd);

        if (errors == 0) $display("ALL %0d TESTS PASSED", ntests);
        else             $display("%0d ERRORS in %0d tests", errors, ntests);
        $finish;
    end
endmodule
