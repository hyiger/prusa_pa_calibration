G1 Z{park_z} F720 ; move print head up
M104 S0 ; turn off hotend
M140 S0 ; turn off heatbed
M107 ; turn off fan
G1 X241 Y170 F3600 ; park
G4 ; wait
M572 S0 ; reset pressure advance (ignored on Marlin)
M900 K0 ; reset Linear Advance
M593 X T2 F0 ; disable input shaping X
M593 Y T2 F0 ; disable input shaping Y
M84 X Y E ; disable motors
; max_layer_z = {max_layer_z}
