M862.3 P "MINI" ; printer model check
M862.1 P{nozzle_dia} ; nozzle check
M862.5 P2 ; g-code level check
M862.6 P"Input shaper" ; FW feature check
M115 U6.4.0+11974
G90 ; use absolute coordinates
M83 ; extruder relative mode
M104 S{mbl_temp} ; set extruder temp for bed leveling
M140 S{bed_temp} ; set bed temp
M109 R{mbl_temp} ; wait for bed leveling temp
M190 S{bed_temp} ; wait for bed temp
M569 S1 X Y ; set stealthchop for X Y
M204 T1250 ; set travel acceleration
G28 ; home all without mesh bed level
G29 ; mesh bed leveling
M104 S{hotend_temp} ; set extruder temp
G92 E0
G1 X0 Y-2 Z3 F2400
M109 S{hotend_temp} ; wait for extruder temp
;
; Intro line
;
G1 X10 Z0.2 F1000
G1 X70 E8 F900
G1 X140 E10 F700
G92 E0
M569 S0 X Y ; set spreadcycle for X Y
M204 T1250 ; restore travel acceleration
M572 W0.06 ; set pressure advance smooth time
M221 S95 ; set flow
