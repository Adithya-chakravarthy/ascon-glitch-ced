# Creates the Vivado project in build/vivado. In the Vivado Tcl console:
#   cd <repo>/vivado
#   source create_project.tcl
# If Vivado cannot find Python, set PY first, e.g.  set PY C:/Python312/python.exe

set root [file normalize [file join [file dirname [info script]] ..]]
if {![info exists PY]} { set PY python }

file mkdir $root/build/gen
puts [exec $PY $root/scripts/gen_gpar.py --k 32 --mode interleave --chunk 6 --out $root/build/gen/ascon_gpar.v]

create_project ascon_glitch $root/build/vivado -part xc7a35tcpg236-1 -force
add_files -norecurse [concat [glob $root/rtl/*.v] [list $root/build/gen/ascon_gpar.v]]
add_files -fileset constrs_1 -norecurse [list $root/constraints/cmod_a7.xdc $root/constraints/glitch_timing.xdc]
add_files -fileset sim_1 -norecurse [glob $root/sim/*.v]
set_property top ascon_uart_top [get_filesets sources_1]
set_property top tb_ascon_aead128 [get_filesets sim_1]
update_compile_order -fileset sources_1
