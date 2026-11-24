## gclk is timed as clk0; the shortened cycle is intentionally outside timing
create_generated_clock -name gclk -source [get_pins u_clk/u_mmcm/CLKOUT1] -divide_by 1 [get_pins u_clk/u_bufg_g/O]

## clk1 and clk2n only clock the gate flip-flops and their phase moves at run time
set_clock_groups -asynchronous \
    -group [concat [get_clocks gclk] [get_clocks -of_objects [get_pins u_clk/u_mmcm/CLKOUT1]]] \
    -group [get_clocks -of_objects [get_pins u_clk/u_mmcm/CLKOUT2]] \
    -group [get_clocks -of_objects [get_pins u_clk/u_mmcm/CLKOUT3B]]

## TDC carry chain
set_false_path -from [get_cells -hier -filter {NAME =~ *u_tdc/launch_q_reg}] -to [get_cells -hier -filter {NAME =~ *u_tdc/taps_q_reg*}]
