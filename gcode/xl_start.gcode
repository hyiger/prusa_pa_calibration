M17 ; enable steppers
M862.3 P "XL" ; printer model check
M862.5 P2 ; g-code level check
M862.6 P"Input shaper" ; FW feature check
M115 U6.2.6+8948
G90 ; use absolute coordinates
M83 ; extruder relative mode
M555 X{m555_x} Y{m555_y} W{m555_w} H{m555_h}
M862.1 P{nozzle_dia} ; nozzle check
M140 S{bed_temp} ; set bed temp
M104 S{mbl_temp} ; set extruder temp for bed leveling
G28 XY ; home carriage
M109 R{mbl_temp} ; wait for bed leveling temp
M84 E ; turn off E motor
G28 Z ; home Z
M104 S70 ; set idle temp
M190 S{bed_temp} ; wait for bed temp
G29 G ; absorb heat
M109 R{mbl_temp} ; wait for MBL temp
; move to nozzle cleanup area
G1 X30 Y-8 Z5 F4800
M302 S155 ; lower cold extrusion limit to 155 C
G1 E-2 F2400 ; retraction
M84 E ; turn off E motor
G29 P9 X30 Y-8 W32 H7
G0 Z10 F480 ; move away in Z
M106 S100 ; cool nozzle
M107 ; stop cooling fan
;
; MBL
;
M84 E ; turn off E motor
G29 P1 ; invalidate mbl and probe print area
G29 P1 X30 Y0 W50 H20 C ; probe near purge place
G29 P3.2 ; interpolate mbl probes
G29 P3.13 ; extrapolate mbl outside probe area
G29 A ; activate mbl
M104 S{hotend_temp} ; set extruder temp
G1 Z10 F720 ; move away in Z
G0 X30 Y-8 F6000 ; move next to the sheet
M109 S{hotend_temp} ; wait for extruder temp
M591 S0 ; disable stuck filament detection
;
; Purge line
;
G92 E0 ; reset extruder position
G0 X30 Y-8 ; move close to the sheet edge
G1 E2 F2400 ; deretraction after the initial one
G0 E10 X40 Z0.2 F500 ; purge
G0 X70 E9 F800 ; purge
G0 X73 Z0.05 F8000 ; wipe, move close to the bed
G0 X76 Z0.2 F8000 ; wipe, move away from the bed
M591 R ; restore stuck filament detection
G92 E0 ; reset extruder position
