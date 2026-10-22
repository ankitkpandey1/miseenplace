# Records docs/assets/demo.gif from the real `mep demo`. Run: scripts/record_demo.sh
Output docs/assets/demo.gif
Set Shell "bash"
Set FontSize 14
Set Width 1100
Set Height 960
Set Padding 14
Set Theme "Dracula"
Set TypingSpeed 60ms
Set Framerate 10

Hide
Type `mep() { command mep "$@" | while IFS= read -r l; do printf '%s\n' "$l"; sleep 0.12; done; }`
Enter
Type `PS1='$ '; clear`
Enter
Show
Sleep 500ms
Type "mep demo"
Sleep 400ms
Enter
Sleep 9s
