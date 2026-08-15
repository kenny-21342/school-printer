#!/usr/bin/osascript
--
-- convert-to-pdf.scpt
--
-- Watches a folder. When an Office file appears:
--   1. Opens it in the matching Microsoft Office app (Word / Excel / PowerPoint)
--   2. Exports as PDF to an "outbox" folder with the same base name
--   3. Moves the original to "done"
--   4. The Pi polls the outbox (via SMB) for the resulting PDF
--
-- Deploy on the iMac. Configure paths in the CONFIG section below.
--
-- Triggered by launchd (see com.school.printer-converter.plist).
-- Runs continuously, polling every 5 seconds.

property POLL_INTERVAL : 5 -- seconds between scans

-- CONFIG ----------------------------------------------------------------
property INBOX:   "/Users/Shared/SchoolPrinter/inbox"
property OUTBOX:  "/Users/Shared/SchoolPrinter/outbox"
property DONE:    "/Users/Shared/SchoolPrinter/done"
property ERRORS:  "/Users/Shared/SchoolPrinter/errors"
-- END CONFIG ------------------------------------------------------------

on run
    ensureFolders()
    log "converter started; watching " & INBOX
    repeat
        try
            processInbox()
        on error errMsg
            log "error: " & errMsg
        end try
        delay POLL_INTERVAL
    end repeat
end run

on ensureFolders()
    tell application "System Events"
        repeat with f in {INBOX, OUTBOX, DONE, ERRORS}
            if not (exists folder f) then
                do shell script "mkdir -p " & quoted form of f
            end if
        end repeat
    end tell
end ensureFolders

on processInbox()
    tell application "System Events"
        set files to every file of folder INBOX whose name extension is in {"doc", "docx", "xls", "xlsx", "ppt", "pptx"}
    end tell
    repeat with aFile in files
        set filePath to POSIX path of aFile
        set fileName to name of aFile
        try
            my convertFile(filePath, fileName)
            do shell script "mv " & quoted form of filePath & " " & quoted form of (DONE & "/" & fileName)
            log "converted: " & fileName
        on error errMsg
            log "failed " & fileName & ": " & errMsg
            do shell script "mv " & quoted form of filePath & " " & quoted form of (ERRORS & "/" & fileName)
        end try
    end repeat
end processInbox

on convertFile(filePath, fileName)
    set ext to my getExtension(fileName)
    set baseName to my getBaseName(fileName)
    set outPath to OUTBOX & "/" & baseName & ".pdf"

    if ext is in {"doc", "docx"} then
        my convertWithWord(filePath, outPath)
    else if ext is in {"xls", "xlsx"} then
        my convertWithExcel(filePath, outPath)
    else if ext is in {"ppt", "pptx"} then
        my convertWithPowerPoint(filePath, outPath)
    else
        error "unsupported extension: " & ext
    end if
end convertFile

on convertWithWord(filePath, outPath)
    tell application "Microsoft Word"
        activate
        set theDoc to open file name filePath
        save as theDoc file name format format PDF file name outPath
        close theDoc saving no
    end tell
end convertWithWord

on convertWithExcel(filePath, outPath)
    tell application "Microsoft Excel"
        activate
        set theBook to open workbook workbook file name filePath
        save workbook as theBook filename outPath file format PDF file format
        close theBook saving no
    end tell
end convertWithExcel

on convertWithPowerPoint(filePath, outPath)
    tell application "Microsoft PowerPoint"
        activate
        set thePres to open filePath
        save thePres in outPath as save as PDF
        close thePres saving no
    end tell
end convertWithPowerPoint

on getExtension(fileName)
    set {oldTID, AppleScript's text item delimiters} to {AppleScript's text item delimiters, "."}
    set parts to text items of fileName
    set AppleScript's text item delimiters to oldTID
    if (count of parts) < 2 then return ""
    return item -1 of parts
end getExtension

on getBaseName(fileName)
    set {oldTID, AppleScript's text item delimiters} to {AppleScript's text item delimiters, "."}
    set parts to text items of fileName
    set AppleScript's text item delimiters to oldTID
    if (count of parts) < 2 then return fileName
    return (items 1 thru -2 of parts) as string
end getBaseName
