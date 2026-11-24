# All builds used in the paper, about 3 min each. Needs the project from create_project.tcl.
#   source build_all.tcl

source [file join [file dirname [info script]] build.tcl]

foreach b {
    {v0        0}
    {v1_sbox   1}
    {v2_rpar   2}
    {v3_i32    3 interleave 32}
    {v3_sta24  3 file sta_sel24.json}
    {v3_sta32  3 file sta_sel32.json}
    {v4_dup    4}
    {v3_i8c    3 interleave 8  6}
    {v3_i16c   3 interleave 16 6}
    {v3_i32c   3 interleave 32 6}
    {v3_i64c   3 interleave 64 6}
    {v5_sbox2  5}
    {v6_crc3   6}
    {v7_crc3r  7}
} {
    build {*}$b
}

# same RTL with two other implementation strategies (other placements)
foreach {suffix strategy} {_s2 Performance_Explore _s3 Area_Explore} {
    build v6_crc3${suffix}  6 - - 0 $strategy
    build v5_sbox2${suffix} 5 - - 0 $strategy
    build v3_i16c${suffix}  3 interleave 16 6 $strategy
}
