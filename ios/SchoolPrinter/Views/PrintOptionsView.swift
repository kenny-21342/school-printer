import SwiftUI
import PDFKit

// MARK: - Page thumbnail cache

@MainActor
final class PreviewCache: ObservableObject {
    @Published var thumbnails: [Int: UIImage] = [:]
    @Published var loadedPages: Set<Int> = []
    @Published var pdfInfo: PdfInfo?
    @Published var spoolFile: String?
    @Published var error: String?

    var api: APIClient
    init(api: APIClient) { self.api = api }

    var pageCount: Int { pdfInfo?.pages ?? 0 }

    /// Local PDF document for on-device rendering (no network needed).
    private var pdfDocument: PDFDocument?
    private var localFileURL: URL?

    func loadInfo(fileURL: URL, filename: String, mime: String) async {
        error = nil; thumbnails.removeAll(); loadedPages.removeAll()
        localFileURL = fileURL
        pdfDocument = nil

        // For PDFs: render locally with PDFKit — instant, no upload needed.
        let accessing = fileURL.startAccessingSecurityScopedResource()
        defer { if accessing { fileURL.stopAccessingSecurityScopedResource() } }

        if let doc = PDFDocument(url: fileURL) {
            self.pdfDocument = doc
            self.pdfInfo = PdfInfo(
                pages: doc.pageCount,
                page_size: nil,
                is_pdf: true,
                spool_file: nil,
                needs_conversion: false
            )
            return
        }

        // Non-PDF (image, text, Office): fall back to server for info.
        do {
            let info = try await api.uploadForInfo(fileURL: fileURL, filename: filename, mimeType: mime)
            self.pdfInfo = info
            self.spoolFile = info.spool_file
        } catch {
            self.error = error.localizedDescription
        }
    }

    /// Render a page locally via PDFKit (no network). Falls back to server for non-PDFs.
    func ensureLoaded(_ page: Int) async {
        guard !loadedPages.contains(page) else { return }
        loadedPages.insert(page)

        // Local PDF rendering
        if let doc = pdfDocument, let pdfPage = doc.page(at: page - 1) {
            let bounds = pdfPage.bounds(for: .mediaBox)
            let scale = min(400.0 / bounds.width, 400.0 / bounds.height)
            let sz = CGSize(width: bounds.width * scale, height: bounds.height * scale)
            let renderer = UIGraphicsImageRenderer(size: sz)
            let img = renderer.image { ctx in
                UIColor.white.setFill()
                ctx.fill(CGRect(origin: .zero, size: sz))
                ctx.cgContext.saveGState()
                ctx.cgContext.translateBy(x: 0, y: sz.height)
                ctx.cgContext.scaleBy(x: scale, y: -scale)
                pdfPage.draw(with: .mediaBox, to: ctx.cgContext)
                ctx.cgContext.restoreGState()
            }
            thumbnails[page] = img
            return
        }

        // Server fallback for non-PDFs
        guard let sf = spoolFile else { loadedPages.remove(page); return }
        do {
            let prev = try await api.fetchPreview(spoolFile: sf, page: page, width: 400)
            if let img = UIImage(data: Data(base64Encoded: prev.image_base64) ?? Data()) {
                thumbnails[page] = img
            }
        } catch {
            loadedPages.remove(page)
        }
    }

    /// Prefetch a window of pages around `center`.
    func prefetchAround(_ center: Int, window: Int = 3) async {
        guard pageCount > 0 else { return }
        let lo = max(1, center - window)
        let hi = min(pageCount, center + window)
        var toFetch: [Int] = []
        for p in lo...hi where !loadedPages.contains(p) { toFetch.append(p) }
        guard !toFetch.isEmpty else { return }

        // Local rendering is fast — just do them all at once
        if pdfDocument != nil {
            for page in toFetch { await ensureLoaded(page) }
            return
        }

        // Server path: bounded concurrency
        await withTaskGroup(of: Void.self) { group in
            var inflight = 0
            let maxConcurrent = 3
            while !toFetch.isEmpty || inflight > 0 {
                while inflight < maxConcurrent, let page = toFetch.popLast() {
                    inflight += 1
                    group.addTask { [weak self] in await self?.ensureLoaded(page) }
                }
                _ = await group.next()
                inflight -= 1
            }
        }
    }

    func thumbnail(for page: Int) -> UIImage? { thumbnails[page] }
}

// MARK: - Page range helpers

enum PageRangeMode: String, CaseIterable, Identifiable {
    case all, range, pick, custom
    var id: String { rawValue }
    var label: String {
        switch self {
        case .all: return "All"
        case .range: return "Range"
        case .pick: return "Pick"
        case .custom: return "Custom"
        }
    }
}

func parseCustomPages(_ text: String, max: Int) -> String? {
    let trimmed = text.trimmingCharacters(in: .whitespaces)
    if trimmed.isEmpty { return nil }
    var seen: Set<Int> = []
    for part in trimmed.split(separator: ",") {
        let p = part.trimmingCharacters(in: .whitespaces)
        if p.contains("-") {
            let bits = p.split(separator: "-")
            guard bits.count == 2,
                  let lo = Int(bits[0]), let hi = Int(bits[1]),
                  lo >= 1, hi <= max, lo <= hi else { return nil }
            (lo...hi).forEach { seen.insert($0) }
        } else {
            guard let n = Int(p), n >= 1, n <= max else { return nil }
            seen.insert(n)
        }
    }
    guard !seen.isEmpty else { return nil }
    return compressPages(seen.sorted())
}

func compressPages(_ pages: [Int]) -> String {
    guard !pages.isEmpty else { return "" }
    var out: [String] = []
    var run = [pages[0]]
    for n in pages.dropFirst() {
        if n == run.last! + 1 { run.append(n) }
        else { out.append(run.count == 1 ? "\(run[0])" : "\(run[0])-\(run.last!)"); run = [n] }
    }
    out.append(run.count == 1 ? "\(run[0])" : "\(run[0])-\(run.last!)")
    return out.joined(separator: ",")
}

func expandPages(_ text: String, max: Int) -> Set<Int> {
    var result: Set<Int> = []
    for part in text.split(separator: ",") {
        let s = part.trimmingCharacters(in: .whitespaces)
        if s.contains("-") {
            let bits = s.split(separator: "-")
            if bits.count == 2, let lo = Int(bits[0]), let hi = Int(bits[1]),
               lo >= 1, hi <= max, lo <= hi {
                result.formUnion(Array(lo...hi))
            }
        } else if let n = Int(s), 1...max ~= n {
            result.insert(n)
        }
    }
    return result
}

// MARK: - Options view

struct PrintOptionsView: View {
    @ObservedObject var preview: PreviewCache
    @Binding var options: PrintOptions

    @State private var rangeMode: PageRangeMode = .all
    @State private var rangeFrom: Int = 1
    @State private var rangeTo: Int = 1
    @State private var customText: String = ""
    @State private var pickedPages: Set<Int> = []
    @State private var carouselIndex: Double = 0    // double for Slider; Int via round()
    @State private var scalePercent: String = ""
    @State private var scalingChoice: String = ""  // mirrors picker: "", "fit", "actual", "shrink", "custom"

    private var pageCount: Int { preview.pageCount }
    private var isPad: Bool { UIDevice.current.userInterfaceIdiom == .pad }

    private var paperSizes = ["", "A4", "A3", "A5", "Letter", "Legal", "Tabloid", "B5"]
    private var orientations: [(String, String)] = [
        ("", "Auto"), ("portrait", "Portrait"), ("landscape", "Landscape"),
    ]
    private var scalings: [(String, String)] = [
        ("", "Default"), ("fit", "Fit to page"), ("actual", "Actual size"), ("shrink", "Shrink to fit"), ("custom", "Custom %"),
    ]
    private var layoutDirections: [(String, String)] = [
        ("", "Left-to-right"), ("ltr", "Left-to-right"), ("rtl", "Right-to-left"),
    ]

    /// The scaling factor to apply to the preview thumbnails (0.01 – 4.0).
    /// Driven by the custom percentage text field — updates live as you type.
    private var previewScale: CGFloat {
        if let pct = Double(scalePercent), (1...400).contains(pct) {
            return CGFloat(pct / 100.0)
        }
        return 1.0
    }

    /// Currently focused page in the carousel (1-indexed).
    private var currentCarouselPage: Int {
        let n = max(1, pageCount)
        return min(n, max(1, Int(round(carouselIndex)) + 1))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            carouselSection
            Divider().padding(.vertical, 6)
            pagesSection
            Divider().padding(.vertical, 6)
            printSection
            Divider().padding(.vertical, 6)
            paperSection
        }
        .padding(.vertical, 8)
        .onChange(of: rangeMode) { _, newMode in
            if newMode == .pick { pickedPages = Set(1...pageCount) }
            syncOption()
        }
        .onChange(of: scalePercent) { _, new in
            if let pct = Int(new), (1...400).contains(pct) {
                options.scaling = String(pct)
            }
        }
    }

    // MARK: Carousel preview (horizontal, ~5 pages visible)

    private var carouselSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Preview").font(.headline)
                Spacer()
                if pageCount > 0 {
                    Text("Page \(currentCarouselPage) of \(pageCount)")
                        .font(.caption).foregroundStyle(.secondary)
                }
                if rangeMode == .pick, !pickedPages.isEmpty {
                    Text("\(pickedPages.count) selected")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .padding(.horizontal)

            if preview.error != nil {
                Text(preview.error!).foregroundStyle(.red).font(.footnote).padding(.horizontal)
            } else if pageCount == 0 {
                HStack { ProgressView(); Text("Loading preview…") }
                    .frame(maxWidth: .infinity).padding()
            } else {
                // Horizontal carousel: shows ~5 pages at a time
                CarouselView(
                    pageCount: pageCount,
                    carouselIndex: $carouselIndex,
                    isSelected: { isSelected($0) },
                    isPickMode: rangeMode == .pick,
                    previewScale: previewScale,
                    isPad: isPad,
                    paperSize: options.paperSize,
                    orientation: options.orientation,
                    preview: preview,
                    onTap: { handleTap($0) }
                )
            }
        }
        .padding(.vertical, 6)
        .task {
            if pageCount > 0 {
                await preview.prefetchAround(1, window: 3)
            }
        }
    }

    // MARK: Pages section

    private var pagesSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Pages").font(.headline).padding(.horizontal)
            Picker("", selection: $rangeMode) {
                ForEach(PageRangeMode.allCases) { m in Text(m.label).tag(m) }
            }
            .pickerStyle(.segmented).padding(.horizontal)

            switch rangeMode {
            case .all:
                Text("All \(pageCount) pages")
                    .foregroundStyle(.secondary).font(.subheadline).padding(.horizontal)
            case .range:
                HStack {
                    Stepper("From \(rangeFrom)", value: $rangeFrom, in: 1...pageCount)
                        .onChange(of: rangeFrom) { _, _ in
                            if rangeTo < rangeFrom { rangeTo = rangeFrom }
                            carouselIndex = Double(rangeFrom - 1)
                            syncOption()
                        }
                    Stepper("To \(rangeTo)", value: $rangeTo, in: rangeFrom...pageCount)
                        .onChange(of: rangeTo) { _, _ in syncOption() }
                }
                .font(.subheadline).padding(.horizontal)
            case .pick:
                Text("Tap pages in the carousel above to toggle them.")
                    .font(.caption).foregroundStyle(.secondary).padding(.horizontal)
                if pickedPages.isEmpty {
                    Text("No pages selected").foregroundStyle(.orange).font(.caption).padding(.horizontal)
                }
            case .custom:
                TextField("e.g. 1,3,5-7", text: $customText)
                    .textInputAutocapitalization(.never)
                    .keyboardType(.numbersAndPunctuation)
                    .textFieldStyle(.roundedBorder)
                    .onChange(of: customText) { _, _ in syncOption() }
                    .padding(.horizontal)
                if !customText.isEmpty, parseCustomPages(customText, max: pageCount) == nil {
                    Text("Invalid page range").foregroundStyle(.red).font(.caption).padding(.horizontal)
                }
            }
        }
        .padding(.vertical, 6)
    }

    // MARK: Print section

    private var printSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Print").font(.headline).padding(.horizontal)
            Stepper("Copies: \(options.copies)", value: $options.copies, in: 1...50).padding(.horizontal)
            Toggle("Double-sided", isOn: $options.duplex).padding(.horizontal)
            Picker("Color", selection: $options.colorMode) {
                Text("Black & White").tag("monochrome")
                Text("Color").tag("color")
            }
            .pickerStyle(.segmented).padding(.horizontal)
        }
        .padding(.vertical, 6)
    }

    // MARK: Paper & scaling section

    private var paperSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Paper & Layout").font(.headline).padding(.horizontal)

            Picker("Paper size", selection: $options.paperSize) {
                ForEach(paperSizes, id: \.self) { s in Text(s.isEmpty ? "Default" : s).tag(s) }
            }
            .pickerStyle(.menu).padding(.horizontal)

            HStack {
                Picker("Orientation", selection: $options.orientation) {
                    ForEach(orientations, id: \.0) { o in Text(o.1).tag(o.0) }
                }
                Picker("Scaling", selection: $scalingChoice) {
                    ForEach(scalings, id: \.0) { s in Text(s.1).tag(s.0) }
                }
                .onChange(of: scalingChoice) { _, new in
                    if new == "custom" {
                        if scalePercent.isEmpty { scalePercent = "100" }
                        options.scaling = scalePercent
                    } else {
                        scalePercent = ""
                        options.scaling = new
                    }
                }
            }
            .pickerStyle(.menu).padding(.horizontal)
            .onAppear {
                // sync scalingChoice from options on first appear
                if Int(options.scaling) != nil { scalingChoice = "custom"; scalePercent = options.scaling }
                else { scalingChoice = options.scaling }
            }

            // Custom percentage text field — appears when "Custom %" is selected
            if options.scaling == "custom" || (Int(options.scaling) != nil) {
                HStack {
                    Text("Scale")
                        .font(.subheadline)
                    TextField("100", text: $scalePercent)
                        .keyboardType(.numberPad)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: isPad ? 100 : 70)
                    Text("%")
                        .font(.subheadline)
                    if let pct = Int(scalePercent), (1...400).contains(pct) {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundStyle(.green)
                    } else if !scalePercent.isEmpty {
                        Image(systemName: "exclamationmark.triangle.fill")
                            .foregroundStyle(.orange)
                    }
                    Spacer()
                    Stepper("", value: Binding(
                        get: { Int(scalePercent) ?? 100 },
                        set: { scalePercent = String($0) }
                    ), in: 1...400, step: 5)
                    .labelsHidden()
                }
                .padding(.horizontal)
                Text("Enter 1–400%. The preview above updates live.")
                    .font(.caption2).foregroundStyle(.secondary).padding(.horizontal)
            }

            Picker("Layout direction", selection: $options.layoutDirection) {
                ForEach(layoutDirections, id: \.0) { d in Text(d.1).tag(d.0) }
            }
            .pickerStyle(.menu).padding(.horizontal)
        }
        .padding(.vertical, 6)
    }

    // MARK: Selection logic

    private func isSelected(_ page: Int) -> Bool {
        switch rangeMode {
        case .all: return true
        case .range: return page >= rangeFrom && page <= rangeTo
        case .pick: return pickedPages.contains(page)
        case .custom: return expandPages(customText, max: pageCount).contains(page)
        }
    }

    private func handleTap(_ page: Int) {
        guard rangeMode == .pick else { return }
        if pickedPages.contains(page) { pickedPages.remove(page) }
        else { pickedPages.insert(page) }
        syncOption()
    }

    private func syncOption() {
        switch rangeMode {
        case .all: options.pageRange = ""
        case .range: options.pageRange = rangeFrom == rangeTo ? "\(rangeFrom)" : "\(rangeFrom)-\(rangeTo)"
        case .pick: options.pageRange = pickedPages.isEmpty ? "" : compressPages(pickedPages.sorted())
        case .custom: options.pageRange = parseCustomPages(customText, max: pageCount) ?? customText
        }
    }
}

// MARK: - Page thumbnail cell (tappable, with live scaling)

struct CarouselView: View {
    let pageCount: Int
    @Binding var carouselIndex: Double
    let isSelected: (Int) -> Bool
    let isPickMode: Bool
    let previewScale: CGFloat
    let isPad: Bool
    let paperSize: String
    let orientation: String
    @ObservedObject var preview: PreviewCache
    let onTap: (Int) -> Void

    var body: some View {
        VStack(spacing: 8) {
            ScrollViewReader { proxy in
                ScrollView(.horizontal, showsIndicators: true) {
                    HStack(spacing: isPad ? 20 : 14) {
                        ForEach(1...pageCount, id: \.self) { page in
                            PageThumbCell(
                                page: page,
                                image: preview.thumbnail(for: page),
                                isSelected: isSelected(page),
                                isPickMode: isPickMode,
                                previewScale: previewScale,
                                isPad: isPad,
                                paperSize: paperSize,
                                orientation: orientation,
                                onTap: { onTap(page) }
                            )
                            .id(page)
                            .task {
                                await preview.ensureLoaded(page)
                                await preview.prefetchAround(page, window: 2)
                            }
                        }
                    }
                    .padding(.horizontal)
                }
                .frame(height: isPad ? 320 : 220)
                .onChange(of: carouselIndex) { _, new in
                    withAnimation(.easeInOut(duration: 0.2)) {
                        proxy.scrollTo(Int(round(new)) + 1, anchor: .center)
                    }
                }
            }

            if pageCount > 5 {
                HStack {
                    Image(systemName: "chevron.left")
                        .foregroundStyle(.secondary)
                        .onTapGesture { carouselIndex = max(0, carouselIndex - 1) }
                    Slider(value: $carouselIndex, in: 0...Double(max(0, pageCount - 1)), step: 1)
                        .tint(.accentColor)
                    Image(systemName: "chevron.right")
                        .foregroundStyle(.secondary)
                        .onTapGesture { carouselIndex = min(Double(pageCount - 1), carouselIndex + 1) }
                }
                .padding(.horizontal)
                .font(.caption)
            }
        }
    }
}

struct PageThumbCell: View {
    let page: Int
    let image: UIImage?
    let isSelected: Bool
    let isPickMode: Bool
    let previewScale: CGFloat
    let isPad: Bool
    let paperSize: String      // "", "A4", "A3", "A5", "Letter", "Legal", "Tabloid", "B5"
    let orientation: String    // "", "portrait", "landscape", "auto"
    let onTap: () -> Void

    private var cellHeight: CGFloat { isPad ? 300 : 210 }
    private var baseWidth: CGFloat { isPad ? 210 : 145 }

    /// Real paper dimensions in mm — used to compute the aspect ratio.
    /// Default (empty) = A4.
    private var paperMM: (CGFloat, CGFloat) {
        switch paperSize {
        case "A3":     return (297, 420)
        case "A4":     return (210, 297)
        case "A5":     return (148, 210)
        case "Letter": return (216, 279)
        case "Legal":  return (216, 356)
        case "Tabloid":return (279, 432)
        case "B5":     return (176, 250)
        default:       return (210, 297)  // A4 default
        }
    }

    /// Effective paper aspect, swapping for landscape.
    private var paperAspect: CGFloat {
        let (w, h) = paperMM
        return orientation == "landscape" ? w / h : h / w
    }

    /// Whether the paper is shown in landscape orientation.
    private var isLandscape: Bool { orientation == "landscape" }

    /// Paper frame dimensions fitted within the cell.
    private var paperFrame: CGSize {
        let maxW = baseWidth
        let maxH = cellHeight - 30   // leave room for page number label
        let aspect = paperAspect
        // try fitting by width first
        var w = maxW
        var h = w * aspect
        if h > maxH { h = maxH; w = h / aspect }
        return CGSize(width: w, height: h)
    }

    var body: some View {
        Button(action: onTap) {
            VStack(spacing: 8) {
                ZStack(alignment: .topTrailing) {
                    let pf = paperFrame

                    // Paper background (white sheet)
                    RoundedRectangle(cornerRadius: 4)
                        .fill(Color.white)
                        .shadow(color: .black.opacity(0.12), radius: 3, x: 1, y: 2)
                        .frame(width: pf.width, height: pf.height)

                    // Page content, scaled and clipped to paper bounds
                    Group {
                        if let img = image {
                            Image(uiImage: img)
                                .resizable()
                                .aspectRatio(contentMode: .fit)
                                .frame(
                                    width: pf.width * previewScale,
                                    height: pf.height * previewScale
                                )
                                .frame(width: pf.width, height: pf.height) // center within paper
                                .clipped()
                        } else {
                            ProgressView()
                                .frame(width: pf.width * 0.5, height: pf.height * 0.5)
                        }
                    }
                    .clipShape(RoundedRectangle(cornerRadius: 4))

                    // Paper outline / selection border
                    RoundedRectangle(cornerRadius: 4)
                        .stroke(isSelected ? Color.accentColor : Color.gray.opacity(0.35),
                                lineWidth: isPad ? 2.5 : 2)
                        .frame(width: pf.width, height: pf.height)

                    // Scale badge — show percentage when not 100%
                    if abs(previewScale - 1.0) > 0.01 {
                        Text("\(Int((previewScale * 100).rounded()))%")
                            .font(.system(size: isPad ? 11 : 9, weight: .semibold, design: .rounded))
                            .padding(.horizontal, 6).padding(.vertical, 2)
                            .background(.ultraThinMaterial, in: Capsule())
                            .padding(4)
                            .frame(maxWidth: .infinity, maxHeight: .infinity,
                                   alignment: .bottomLeading)
                    }

                    // Pick badge
                    if isPickMode {
                        ZStack {
                            Circle()
                                .fill(isSelected ? Color.accentColor : Color.white.opacity(0.9))
                                .frame(width: isPad ? 28 : 22, height: isPad ? 28 : 22)
                                .overlay(Circle().stroke(Color.white.opacity(0.4), lineWidth: 1))
                            Image(systemName: isSelected ? "checkmark" : "")
                                .font(.system(size: isPad ? 13 : 11, weight: .bold))
                                .foregroundStyle(.white)
                        }
                        .padding(5)
                    }
                }
                .frame(height: cellHeight - 20)
                .opacity(isPickMode && !isSelected ? 0.4 : 1.0)

                Text("\(page)")
                    .font(.caption2)
                    .foregroundStyle(isSelected ? .primary : .secondary)
            }
            .frame(width: baseWidth + 12)
        }
        .buttonStyle(.plain)
        .disabled(!isPickMode)
    }
}
