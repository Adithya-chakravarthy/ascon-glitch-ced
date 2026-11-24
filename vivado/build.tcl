# Builds one configuration in the open project and records what the paper needs:
#   build/bits/<label>.bit              bitstream
#   build/bits/<label>_groups.json      parity groups (VARIANT 3)
#   build/sta/<label>_state.csv         worst setup slack into each of the 320 state bits
#   build/sta/<label>_checker.csv       worst setup slack into each checker register
#   build/sta/<label>_coreb.csv         second core (VARIANT 4)
#   build/cost.csv                      core/checker LUTs and FFs after synthesis and
#                                       implementation, WNS
# Usage:
#   source build.tcl
#   build v3_i32c 3 interleave 32 6
#   build v3_sta24 3 file sta_sel24.json
#   build v6_crc3_s3 6 - - 0 Area_Explore

set root [file normalize [file join [file dirname [info script]] ..]]
if {![info exists PY]} { set PY python }
file mkdir $root/build/bits $root/build/sta

proc util_numbers {rpt} {
    set luts "?"
    set ffs  "?"
    regexp {Slice LUTs\*?\s*\|\s*(\d+)} $rpt -> luts
    regexp {Slice Registers\s*\|\s*(\d+)} $rpt -> ffs
    return [list $luts $ffs]
}

# LUTs/FFs of the Ascon core(s) and of the checker alone, in the open design
proc core_and_checker {} {
    set cl 0; set cf 0; set kl 0; set kf 0
    foreach c [get_cells -hier -quiet -filter {REF_NAME =~ ascon_aead128_enc* || ORIG_REF_NAME =~ ascon_aead128_enc*}] {
        lassign [util_numbers [report_utilization -cells $c -return_string]] a b
        if {[string is integer -strict $a]} { incr cl $a }
        if {[string is integer -strict $b]} { incr cf $b }
    }
    foreach c [get_cells -hier -quiet -filter {REF_NAME =~ ascon_sbox_par* || ORIG_REF_NAME =~ ascon_sbox_par* || REF_NAME =~ ascon_sbox_sig* || ORIG_REF_NAME =~ ascon_sbox_sig* || REF_NAME =~ ascon_gpar_check* || ORIG_REF_NAME =~ ascon_gpar_check*}] {
        lassign [util_numbers [report_utilization -cells $c -return_string]] a b
        if {[string is integer -strict $a]} { incr kl $a }
        if {[string is integer -strict $b]} { incr kf $b }
    }
    return [list $cl $cf $kl $kf]
}

# worst setup slack of the round datapath into each state bit of one core
proc state_slack {core out} {
    set src [get_cells -hier -quiet -filter "NAME =~ *${core}/x*_reg* || NAME =~ *${core}/rnd_reg*"]
    set dst [get_cells -hier -quiet -filter "NAME =~ *${core}/x*_reg*"]
    set re $core
    append re {/x([0-4])_reg\[([0-9]+)\]$}
    set rows {}
    foreach c $dst {
        if {![regexp $re $c -> w b]} { continue }
        set p [get_timing_paths -from $src -to $c -max_paths 1 -nworst 1 -setup]
        if {[llength $p] == 0} { continue }
        lappend rows [list [expr {64 * $w + $b}] $w $b [get_property SLACK $p] [get_property DATAPATH_DELAY $p] [get_property LOGIC_LEVELS $p]]
    }
    set fh [open $out w]
    puts $fh "bit_index,word,bit,datapath_slack_ns,data_path_delay_ns,logic_levels"
    foreach r [lsort -integer -index 0 $rows] { puts $fh [join $r ","] }
    close $fh
    return [llength $rows]
}

# worst setup slack into every checker register (pred_q, chunk_q, err_q, ...)
proc checker_slack {out} {
    set cells [get_cells -hier -quiet -filter {REF_NAME =~ FD* && (NAME =~ *u_gchk/* || NAME =~ *g_round_par* || NAME =~ *g_sbox_sig*)}]
    set fh [open $out w]
    puts $fh "register,index,slack_ns,data_path_delay_ns,logic_levels"
    set n 0
    foreach c $cells {
        set idx -1
        regexp {\[([0-9]+)\]} [file tail $c] -> idx
        set p [get_timing_paths -to $c -max_paths 1 -nworst 1 -setup]
        if {[llength $p] == 0} { continue }
        regsub {\[[0-9]+\].*$} [file tail $c] {} base
        regsub {^.*\.} $base {} base
        puts $fh "$base,$idx,[get_property SLACK $p],[get_property DATAPATH_DELAY $p],[get_property LOGIC_LEVELS $p]"
        incr n
    }
    close $fh
    return $n
}

proc run_impl {} {
    reset_run synth_1
    launch_runs impl_1 -to_step write_bitstream -jobs 4
    wait_on_run impl_1
    if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
        error "implementation did not finish"
    }
}

proc build {label variant {mode -} {k -} {chunk 0} {strategy ""}} {
    global root PY
    puts "=== $label (VARIANT $variant) ==="
    foreach d [get_designs] { catch {current_design $d; close_design} }

    if {$variant == 3} {
        if {$mode eq "file"} {
            set gf $k
            if {![file exists $gf]} { set gf $root/data/groups/$k }
            set opts [list --mode file --groups $gf]
        } else {
            set opts [list --k $k --mode $mode --seed 1]
        }
        if {$chunk > 0} { lappend opts --chunk $chunk }
        puts [exec $PY $root/scripts/gen_gpar.py {*}$opts --out $root/build/gen/ascon_gpar.v]
    }
    set_property generic "VARIANT=$variant" [get_filesets sources_1]
    set old_strategy [get_property strategy [get_runs impl_1]]
    if {$strategy ne ""} { set_property strategy $strategy [get_runs impl_1] }

    # on Windows a run sometimes fails to start when memory is short; retry once
    if {[catch run_impl err]} {
        puts "retrying ($err)"
        foreach d [get_designs] { catch {current_design $d; close_design} }
        after 20000
        run_impl
    }
    set_property strategy $old_strategy [get_runs impl_1]

    set runs [file join [get_property DIRECTORY [current_project]] "[current_project].runs"]
    set used "?"
    set log [file join $runs synth_1 runme.log]
    if {[file exists $log]} {
        set fh [open $log r]
        regexp {VARIANT bound to: (\d+)} [read $fh] -> used
        close $fh
    }
    if {$used ne $variant} { puts "WARNING: synthesis log shows VARIANT $used" }

    file copy -force [file join $runs impl_1 ascon_uart_top.bit] $root/build/bits/$label.bit
    if {$variant == 3} { file copy -force $root/build/gen/ascon_gpar.json $root/build/bits/${label}_groups.json }

    open_run impl_1
    set impl [core_and_checker]
    set wns [get_property SLACK [lindex [get_timing_paths -max_paths 1 -nworst 1 -setup] 0]]
    puts "state bits: [state_slack u_core $root/build/sta/${label}_state.csv]"
    if {[lsearch -exact {1 2 3 5 6 7} $variant] >= 0} {
        puts "checker registers: [checker_slack $root/build/sta/${label}_checker.csv]"
    }
    if {$variant == 4} { state_slack u_core_b $root/build/sta/${label}_coreb.csv }
    close_design

    open_run synth_1 -name synth_view
    set synth [core_and_checker]
    close_design

    set f $root/build/cost.csv
    set new [expr {![file exists $f]}]
    set fh [open $f a]
    if {$new} {
        puts $fh "label,variant,strategy,synth_core_luts,synth_core_ffs,synth_checker_luts,synth_checker_ffs,impl_core_luts,impl_core_ffs,impl_checker_luts,impl_checker_ffs,wns_ns"
    }
    set sname [expr {$strategy eq "" ? "default" : $strategy}]
    puts $fh [join [concat [list $label $variant $sname] $synth $impl [list $wns]] ","]
    close $fh
    puts "core+checker LUT/FF: $impl, WNS $wns ns"
}
