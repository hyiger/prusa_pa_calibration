M17 ; enable steppers
M862.1 P{nozzle_dia} ; nozzle check
M862.3 P "COREONEL" ; printer model check
M862.5 P2 ; g-code level check
M862.6 P"Input shaper" ; FW feature check
M115 U6.5.1+12574
M555 X{m555_x} Y{m555_y} W{m555_w} H{m555_h}
G90 ; use absolute coordinates
M83 ; extruder relative mode
M140 S{bed_temp} ; set bed temp
M106 P5 R A125 B10 ; turn on bed fans with fade
M109 R{mbl_temp} ; preheat nozzle to no-ooze temp for bed leveling
M84 E ; turn off E motor
G28 Q ; home all without mesh bed level
G1 Z20 F720 ; lift bed to optimal bed fan height
M141 S0 ; set nominal chamber temp
{cool_fan}
M190 R{bed_temp} ; wait for bed temp
M107
M109 R{mbl_temp} ; wait for MBL temp
M302 S155 ; lower cold extrusion limit to 155 C
G1 E-2 F2400 ; retraction
M84 E ; turn off E motor
G29 P9 X208 Y-2.5 W32 H4
;
; MBL
;
M84 E ; turn off E motor
G29 P1 ; invalidate mbl and probe print area
G29 P1 X150 Y0 W100 H20 C ; probe near purge place
G29 P3.2 ; interpolate mbl probes
G29 P3.13 ; extrapolate mbl outside probe area
G29 A ; activate mbl
; prepare for purge
M104 S{hotend_temp}
G0 X249 Y-2.5 Z15 F4800 ; move away and ready for the purge
M109 S{hotend_temp}
G92 E0
M569 S0 E ; set spreadcycle mode for extruder
M591 S0 ; disable stuck filament detection
;
; Purge line
;
G92 E0 ; reset extruder position
G1 E2 F2400 ; deretraction after the initial one
G0 E5 X235 Z0.2 F500 ; purge
G0 X225 E4 F500 ; purge
G0 X215 E4 F650 ; purge
G0 X205 E4 F800 ; purge
G0 X202 Z0.05 F8000 ; wipe, move close to the bed
G0 X199 Z0.2 F8000 ; wipe, move away from the bed
M591 R ; restore stuck filament detection
G92 E0
M221 S100 ; set flow to 100%
