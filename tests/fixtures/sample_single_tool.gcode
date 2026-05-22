; PrusaSlicer 2.7.0 — sample fixture for Phase 6 metadata parser.
; This stub is intentionally tiny; only the slicer comment block at
; the end is read by app.shared.gcode_metadata.
G28 ; home
G1 X10 Y10 F3000
G1 X20 Y20
G1 X30 Y30
M104 S215 ; nozzle temp
M140 S60  ; bed temp
G1 Z0.2
M84
; --- slicer metadata block ---
; filament used [mm] = 1234.56
; filament used [g] = 4.5
; filament_type = PLA
; nozzle_diameter = 0.4
; layer_height = 0.2
; fill_density = 15%
; nozzle_temperature = 215
; bed_temperature = 60
