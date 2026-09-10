#!/usr/bin/osascript
--
-- convert-to-pdf.applescript <input-file> <output.pdf>
--
-- Converts one Office document to PDF by driving the real Microsoft Office
-- app, and exits. Called as a subprocess by server/app/office.py.
--
-- This replaces the old folder-watching converter, which existed only because
-- the API lived on a different machine from Office: it polled an inbox, wrote
-- to an outbox, and the Pi shuffled files back and forth over SMB. Now that the
-- API runs on this Mac, conversion is a plain function call and all of that --
-- the inbox, outbox, done and errors folders, the 5-second poll loop -- is gone.
--
-- Word, Excel and PowerPoint are GUI applications, so this only works inside a
-- logged-in Aqua session. That is why the service is a LaunchAgent and why the
-- iMac needs automatic login. Each app also needs a one-time Automation grant
-- (System Settings -> Privacy & Security -> Automation).
--
-- Errors are raised, not logged: a non-zero exit and the message on stderr are
-- what office.py turns into the job's error text.

on run argv
    if (count of argv) < 2 then
        error "usage: convert-to-pdf.applescript <input-file> <output.pdf>"
    end if
    set inputPath to item 1 of argv
    set outputPath to item 2 of argv

    tell application "System Events"
        if not (exists file inputPath) then error "input file not found: " & inputPath
    end tell

    set ext to my getExtension(inputPath)
    if ext is in {"doc", "docx", "rtf"} then
        my convertWithWord(inputPath, outputPath)
    else if ext is in {"xls", "xlsx"} then
        my convertWithExcel(inputPath, outputPath)
    else if ext is in {"ppt", "pptx"} then
        my convertWithPowerPoint(inputPath, outputPath)
    else
        error "unsupported extension: " & ext
    end if

    return outputPath
end run

on convertWithWord(filePath, outPath)
    tell application "Microsoft Word"
        set theDoc to open file name filePath with read only
        try
            save as theDoc file name outPath file format format PDF
        on error errMsg number errNum
            close theDoc saving no
            error errMsg number errNum
        end try
        close theDoc saving no
    end tell
end convertWithWord

on convertWithExcel(filePath, outPath)
    tell application "Microsoft Excel"
        set theBook to open workbook workbook file name filePath
        try
            save workbook as theBook filename outPath file format PDF file format
        on error errMsg number errNum
            close theBook saving no
            error errMsg number errNum
        end try
        close theBook saving no
    end tell
end convertWithExcel

on convertWithPowerPoint(filePath, outPath)
    tell application "Microsoft PowerPoint"
        set thePres to open filePath
        try
            save thePres in outPath as save as PDF
        on error errMsg number errNum
            close thePres saving no
            error errMsg number errNum
        end try
        close thePres saving no
    end tell
end convertWithPowerPoint

on getExtension(filePath)
    set {oldTID, AppleScript's text item delimiters} to {AppleScript's text item delimiters, "."}
    set parts to text items of filePath
    set AppleScript's text item delimiters to oldTID
    if (count of parts) < 2 then return ""
    set ext to item -1 of parts
    return my toLower(ext)
end getExtension

on toLower(theText)
    set upperChars to "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    set lowerChars to "abcdefghijklmnopqrstuvwxyz"
    set out to ""
    repeat with c in the characters of theText
        set i to offset of c in upperChars
        if i > 0 then
            set out to out & character i of lowerChars
        else
            set out to out & c
        end if
    end repeat
    return out
end toLower
