import SwiftUI
import UniformTypeIdentifiers

struct PrintView: View {
    @EnvironmentObject var api: APIClient

    @State private var pickedURL: URL?
    @State private var pickedName: String = ""
    @State private var pickedMime: String = "application/octet-stream"
    @StateObject private var previewCache = PreviewCache()
    @State private var options = PrintOptions()
    @State private var showPicker = false
    @State private var resultMessage: String?
    @State private var resultIsError = false

    var body: some View {
        Group {
            if UIDevice.current.userInterfaceIdiom == .pad {
                iPadLayout
            } else {
                iPhoneLayout
            }
        }
        .navigationTitle("Print")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { showPicker = true } label: { Label("Pick", systemImage: "folder.badge.plus") }
            }
        }
        .sheet(isPresented: $showPicker) {
            DocPicker { url, name, mime in
                self.pickedURL = url
                self.pickedName = name
                self.pickedMime = mime
                self.resultMessage = nil
                self.options = PrintOptions()   // don't carry settings across files
                Task { await previewCache.loadInfo(fileURL: url) }
            }
        }
    }

    // MARK: iPad — two-column: preview on left, options on right
    private var iPadLayout: some View {
        ScrollView {
            VStack(spacing: 20) {
                fileCard
                if pickedURL != nil {
                    HStack(alignment: .top, spacing: 20) {
                        CardView {
                            PrintOptionsView(preview: previewCache, options: $options)
                        }
                        .frame(maxWidth: .infinity)

                        VStack(spacing: 14) {
                            printButton
                            if let msg = resultMessage { resultView(msg) }
                        }
                        .frame(width: 280)
                    }
                    .padding(.horizontal, 24)
                } else {
                    emptyPicker
                        .padding(.top, 40)
                }
            }
            .padding(.vertical, 20)
            .frame(maxWidth: 1100)
            .frame(maxWidth: .infinity)
        }
    }

    // MARK: iPhone — single scrolling column
    private var iPhoneLayout: some View {
        ScrollView {
            VStack(spacing: 18) {
                fileCard
                if pickedURL != nil {
                    CardView {
                        PrintOptionsView(preview: previewCache, options: $options)
                    }
                    .padding(.horizontal)
                    printButton
                } else {
                    emptyPicker
                }
                if let msg = resultMessage { resultView(msg) }
            }
            .padding(.vertical, 16)
        }
    }

    // MARK: Shared sub-views

    private var fileCard: some View {
        Group {
            if pickedURL != nil {
                CardView {
                    HStack {
                        Image(systemName: iconName(for: pickedMime))
                            .font(.title2).foregroundStyle(.tint)
                        VStack(alignment: .leading) {
                            Text(pickedName).font(.headline).lineLimit(1)
                            Text(humanMime(pickedMime)).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button { clearPick() } label: {
                            Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary)
                        }
                    }
                    .padding()
                }
                .padding(.horizontal)
            }
        }
    }

    private var printButton: some View {
        Button {
            Task { await submit() }
        } label: {
            HStack {
                if api.isLoading { ProgressView().controlSize(.small).tint(.white) }
                Label("Print", systemImage: "printer.fill").fontWeight(.semibold)
            }
            .frame(maxWidth: .infinity).padding(.vertical, 12)
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.large)
        .disabled(!canPrint)
        .padding(.horizontal)
    }

    private func resultView(_ msg: String) -> some View {
        Text(msg)
            .font(.callout)
            .foregroundStyle(resultIsError ? .red : .green)
            .multilineTextAlignment(.center).padding(.horizontal)
    }

    private var emptyPicker: some View {
        VStack(spacing: 14) {
            Image(systemName: "tray.and.arrow.up")
                .font(.system(size: 60, weight: .ultraLight)).foregroundStyle(.tertiary)
            Text("Pick a file to print").font(.headline)
            Text("PDF, JPEG, PNG, plain text, Word, Excel and PowerPoint.\nOffice files are converted to PDF on the print server.")
                .multilineTextAlignment(.center).foregroundStyle(.secondary).padding(.horizontal)
        }
        .padding(.top, 40)
    }

    /// Printable when a file is picked and any page range the user typed is
    /// valid for that document.
    ///
    /// The previous version mixed && and || across three clauses; operator
    /// precedence made the trailing split-count test almost always true, so it
    /// only worked by accident and was unreadable. parseCustomPages already
    /// validates every supported form ("3", "1-5", "1,3,5-7"), so defer to it.
    private var canPrint: Bool {
        guard pickedURL != nil, !api.isLoading else { return false }
        let range = options.pageRange.trimmingCharacters(in: .whitespaces)
        guard !range.isEmpty else { return true }
        // Only validate when we actually know the page count; for files with no
        // known count the range is left empty by PrintOptionsView anyway.
        guard previewCache.pageCount > 0 else { return true }
        return parseCustomPages(range, max: previewCache.pageCount) != nil
    }

    private func clearPick() {
        pickedURL = nil; pickedName = ""
        previewCache.reset()
    }

    private func submit() async {
        guard let url = pickedURL else { return }
        resultMessage = nil; resultIsError = false
        do {
            let job = try await api.uploadAndPrint(
                fileURL: url, filename: pickedName, mimeType: pickedMime, options: options
            )
            // An Office file has not reached the printer yet — saying "sent to
            // printer" for a job that is still being converted is how a student
            // ends up standing at the tray waiting for nothing.
            resultMessage = job.status == "converting"
                ? "Uploaded (job #\(job.id)). Converting to PDF — it will print in a few seconds; watch My Jobs."
                : "Sent to printer (job #\(job.id)). Status: \(job.status)."
            clearPick()
        } catch {
            resultIsError = true
            resultMessage = error.localizedDescription
        }
    }

    private func iconName(for mime: String) -> String {
        if mime.contains("pdf") { return "doc.richtext" }
        if mime.contains("image") { return "photo" }
        if mime.contains("sheet") || mime.contains("excel") { return "tablecells" }
        if mime.contains("presentation") || mime.contains("powerpoint") { return "rectangle.on.rectangle" }
        if mime.contains("word") { return "doc.text" }
        return "doc"
    }

    private func humanMime(_ mime: String) -> String {
        switch mime {
        case "application/pdf": return "PDF"
        case "image/jpeg": return "JPEG image"
        case "image/png": return "PNG image"
        case "text/plain": return "Plain text"
        case "application/msword",
             "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            return "Word document — converted to PDF on the server"
        case "application/vnd.ms-excel",
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
            return "Excel workbook — converted to PDF on the server"
        case "application/vnd.ms-powerpoint",
             "application/vnd.openxmlformats-officedocument.presentationml.presentation":
            return "PowerPoint deck — converted to PDF on the server"
        default: return mime
        }
    }
}

// MARK: - Doc picker bridge

struct DocPicker: UIViewControllerRepresentable {
    let onPick: (URL, String, String) -> Void

    func makeUIViewController(context: Context) -> UIDocumentPickerViewController {
        // Only formats the server can actually print. Word, Excel and
        // PowerPoint are here because the server converts them to PDF itself
        // now that it runs on the iMac that has Office installed. Pages,
        // Numbers and Keynote stay out: nothing converts those, and offering
        // them would let a student pick a file the server then rejects.
        let officeTypes: [UTType] = [
            "doc", "docx", "xls", "xlsx", "ppt", "pptx",
        ].compactMap { UTType(filenameExtension: $0) }
        let types: [UTType] = [.pdf, .jpeg, .png, .plainText] + officeTypes
        let picker = UIDocumentPickerViewController(forOpeningContentTypes: types, asCopy: true)
        picker.allowsMultipleSelection = false
        picker.delegate = context.coordinator
        return picker
    }
    func updateUIViewController(_ vc: UIDocumentPickerViewController, context: Context) {}
    func makeCoordinator() -> Coordinator { Coordinator(onPick: onPick) }

    final class Coordinator: NSObject, UIDocumentPickerDelegate {
        let onPick: (URL, String, String) -> Void
        init(onPick: @escaping (URL, String, String) -> Void) { self.onPick = onPick }

        func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
            guard let url = urls.first else { return }
            let name = url.lastPathComponent
            let mime: String
            if let ut = UTType(filenameExtension: url.pathExtension) {
                mime = ut.preferredMIMEType ?? "application/octet-stream"
            } else {
                mime = "application/octet-stream"
            }
            onPick(url, name, mime)
        }
    }
}

// MARK: - Card helper

struct CardView<Content: View>: View {
    @ViewBuilder var content: Content
    var body: some View {
        content
            .background(.regularMaterial)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(Color.primary.opacity(0.06), lineWidth: 1)
            )
    }
}
