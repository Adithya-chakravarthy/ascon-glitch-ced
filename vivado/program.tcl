# Programs the board with build/bits/<BIT>.bit
#   set BIT v3_i32c
#   source program.tcl

set root [file normalize [file join [file dirname [info script]] ..]]
set f $root/build/bits/$BIT.bit
if {![file exists $f]} { error "no bitstream $f" }

open_hw_manager
catch {connect_hw_server}
catch {open_hw_target}
set dev [lindex [get_hw_devices xc7a35t_0] 0]
if {$dev eq ""} { error "board not found" }
set_property PROGRAM.FILE $f $dev
program_hw_devices $dev
puts "programmed $BIT"
