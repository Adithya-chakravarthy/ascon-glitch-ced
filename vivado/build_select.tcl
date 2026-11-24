# Timing-selected checking: the N slowest state bits are checked exactly and the rest by
# two interleaved parity groups, with pipelined prediction. After every build the bits
# are selected again from that build's own timing, until the selection stops changing.
# (For N = 24 it had not settled after three passes.) Run v0 first.
#   set N 24
#   source build_select.tcl

source [file join [file dirname [info script]] build.tcl]
if {![info exists N]} { set N 24 }
set passes 3
set gdir $root/build/groups
file mkdir $gdir

set slack $root/build/sta/v0_state.csv
if {![file exists $slack]} { set slack $root/data/sta/v0_state.csv }
puts [exec $PY $root/scripts/select_bits.py --slack $slack --n $N --rest 2 --out $gdir/fix${N}_p0.json]

for {set p 1} {$p <= $passes} {incr p} {
    set q [expr {$p - 1}]
    build v3_fix${N}_p$p 3 file $gdir/fix${N}_p$q.json 6
    set msg [exec $PY $root/scripts/select_bits.py --slack $root/build/sta/v3_fix${N}_p${p}_state.csv \
                 --n $N --rest 2 --out $gdir/fix${N}_p$p.json --prev $gdir/fix${N}_p$q.json]
    puts $msg
    if {[string match *SAME* $msg] || [string match *GOOD* $msg]} { break }
}
