G1 Z{park_z} F720 ; move bed down
M104 S0 ; turn off hotend
M140 S0 ; turn off heatbed
M107 ; turn off fan
G1 X6 Y350 F6000 ; park
G4 ; wait
M900 K0 ; reset Linear Advance
M142 S36 ; reset heatbreak target temp
M221 S100 ; reset flow percentage
M84 ; disable motors
; max_layer_z = {max_layer_z}
