G1 Z{park_z} F720 ; move print head up
M104 S0 ; turn off hotend
M140 S0 ; turn off heatbed
M141 S0 ; disable chamber temp control
M107 ; turn off fan
M107 P5 ; turn off bed fans
G1 X290 Y295 F10200 ; park
G4 ; wait
M572 S0 ; reset pressure advance (ignored on Marlin)
M900 K0 ; reset Linear Advance
M84 X Y E ; disable motors
; max_layer_z = {max_layer_z}
