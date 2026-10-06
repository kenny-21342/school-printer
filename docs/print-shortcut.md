# Print Shortcut (for iPads that cannot install the profile)

Uploads the file you share to the iMac, which prints it under your school ID.
Build it once, then share it with an iCloud link; each student pastes their own
key when they add it.

## Build (on an iPad or iPhone, Shortcuts app)

New Shortcut, name **Print at School**, then in Shortcut settings turn on
**Show in Share Sheet** and accept **PDF**, **Images**, **Text** and **Media**.
Add these actions in order:

1. **Receive** input from Share Sheet (set above).
2. **Text** → paste the key. In the Share dialog's **Import Questions** make this
   Text the question *"Paste your print key"*, so each student is asked on import.
3. **Get Contents of URL**
   - URL: `http://<imac address>:8000/print`
   - Method: **POST**
   - Headers: `X-Print-Key` = the Text from step 2
   - Request Body: **Form**; add a **File** field named `file` = *Shortcut Input*
     (optional text fields: `copies`, `duplex` = true/false, `page_range`)
4. **Get Dictionary Value** for `status` from the result, then **Show Result**
   (or **Show Notification**) so the student sees `pending` / an error message.

Word, Excel and PowerPoint files are converted on the iMac; Pages, Numbers and
Keynote must be exported to PDF first.

## Hand out

Share → Copy iCloud Link. Each approved student's page
(`/register/<their code>`) shows their key; they paste it when adding the Shortcut.

The key is a password for printing as that student: keep it private, and
`tools/school-print revoke <id>` kills it.
