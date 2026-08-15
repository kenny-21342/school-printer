import SwiftUI
import UniformTypeIdentifiers

struct PrintView: View {
    @EnvironmentObject var api: APIClient

    @State private var pickedURL: URL?
    @State private var pickedName: String = ""
    @State private var pickedMime: String = "application/octet-stream"
    @StateObject private var previewCache: PreviewCache
    @State private var options = PrintOptions()
    @State private var showPicker = false
    @State private var resultMessage: String?
    @State private var resultIsError = false

    init() {
        // init previewCache with a placeholder api; replaced in body via environment
        let placeholder = APIClient()
        _previewCache = StateObject(wrappedValue: PreviewCache(api: placeholder))
    }

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
                previewCache.api = api
                Task { await previewCache.loadInfo(fileURL: url, filename: name, mime: mime) }
            }
        }
    }

    // MARK: iPad — two-column: preview on left, options on right
    private var iPadLayout: some View {
        ScrollView {
            VStack(spacing: 20) {
                fileCard
                if let info = previewCache.pdfInfo, info.needs_conversion {
                    conversionNotice
                }
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
                if let info = previewCache.pdfInfo, info.needs_conversion {
                    conversionNotice
                }
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

    private var conversionNotice: some View {
        CardView {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "clock.arrow.circlepath").foregroundStyle(.blue)
                VStack(alignment: .leading, spacing: 4) {
                    Text("Office conversion coming soon").font(.subheadline.bold())
                    Text("This document will print once the conversion iMac is connected.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }.padding()
        }.padding(.horizontal)
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
            Text("PDF, Word, Excel, PowerPoint, images and text are supported.")
                .multilineTextAlignment(.center).foregroundStyle(.secondary).padding(.horizontal)
        }
        .padding(.top, 40)
    }

    private var canPrint: Bool {
        guard pickedURL != nil else { return false }
        if api.isLoading { return false }
        // PDF path needs a valid page range if user picked custom
        if let info = previewCache.pdfInfo, info.is_pdf, info.pages > 0,
           options.pageRange.contains(where: { !$0.isWhitespace }),
           parseCustomPages(options.pageRange, max: info.pages) == nil,
           options.pageRange.split(separator: "-").count != 2
               || options.pageRange.split(separator: "-").compactMap({ Int($0) }).count != 2 {
            // allow plain "from-to" too
            return false
        }
        return true
    }

    private func clearPick() {
        pickedURL = nil; pickedName = ""; resultMessage = nil
        previewCache.pdfInfo = nil; previewCache.thumbnails.removeAll()
    }

    private func submit() async {
        guard let url = pickedURL else { return }
        resultMessage = nil; resultIsError = false
        do {
            let job = try await api.uploadAndPrint(
                fileURL: url, filename: pickedName, mimeType: pickedMime, options: options
            )
            if job.needs_conversion {
                resultMessage = "Queued — Office conversion will complete when the iMac is online (job #\(job.id))."
            } else {
                resultMessage = "Sent to printer (job #\(job.id)). Status: \(job.status)."
            }
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
        default: return mime
        }
    }
}

// MARK: - Doc picker bridge

struct DocPicker: UIViewControllerRepresentable {
    let onPick: (URL, String, String) -> Void

    func makeUIViewController(context: Context) -> UIDocumentPickerViewController {
        let types: [UTType] = [
            .pdf, .image, .plainText, .rtf,
            UTType(filenameExtension: "doc") ?? .data,
            UTType(filenameExtension: "docx") ?? .data,
            UTType(filenameExtension: "xls") ?? .data,
            UTType(filenameExtension: "xlsx") ?? .data,
            UTType(filenameExtension: "ppt") ?? .data,
            UTType(filenameExtension: "pptx") ?? .data,
        ]
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
