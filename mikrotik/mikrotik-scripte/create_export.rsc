## Script Version 3, https://github.com/pos-ei-don/ai_mainprojekt/edit/master/subprojects/mikrotik/mikrotik-scripte/create_export.rsc

:local date [/system clock get date];
:local time [/system clock get time];
:local hour [:pick $time 0 2];
:local minute [:pick $time 3 5];
:local second [:pick $time 6 8];
:local identity [/system identity get name];
:local myVer [/system resource get version] 
#:log info "Originav Time: $time";
:local filename "backup_$identity_$myVer_$date_$hour$minute$second";
#:local filename "backup_$identity_$myVer_$newdate_$hour$minute$second";
#:log info "Backup Filename: $filename";
/export file="$filename";
/system backup save name="$filename";
# Doku zum exportieren dieses Scripts
#/system script export file=create_export_v2b.rsc
