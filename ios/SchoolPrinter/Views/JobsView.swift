import SwiftUI

struct JobsView: View {
    @EnvironmentObject var api: APIClient

    @State private var jobs: [Job] = []
    @State private var loadError: String?
    @State private var showAllUsers = false

    var body: some View {
        List {
            if api.currentUser?.is_admin == true {
                Toggle("Show all users", isOn: $showAllUsers)
                    .onChange(of: showAllUsers) { _, _ in Task { await load() } }
            }

            if jobs.isEmpty {
                ContentUnavailableView(
                    "No jobs yet",
                    systemImage: "tray",
                    description: Text("Printed documents will appear here.")
                )
                .listRowSeparator(.hidden)
                .listRowBackground(Color.clear)
            } else {
                ForEach(jobs) { job in
                    NavigationLink(value: job.id) {
                        JobRow(job: job)
                    }
                }
            }

            if let err = loadError {
                Text(err).foregroundStyle(.red).font(.footnote)
            }
        }
        .navigationTitle("My Jobs")
        .navigationDestination(for: Int.self) { jobId in
            JobDetailView(jobId: jobId)
        }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    Task { await load() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
            }
        }
        .refreshable { await load() }
        .task {
            await load()
            await pollWhileConverting()
        }
    }

    /// Same reasoning as JobDetailView: an Office job flips from "Converting…"
    /// to "Pending" on the server's schedule, not the user's.
    private func pollWhileConverting() async {
        while !Task.isCancelled && jobs.contains(where: { $0.status == "converting" }) {
            try? await Task.sleep(for: .seconds(3))
            if Task.isCancelled { return }
            await load()
        }
    }

    @MainActor
    private func load() async {
        loadError = nil
        do {
            jobs = try await api.listJobs(allUsers: showAllUsers)
        } catch {
            loadError = error.localizedDescription
        }
    }
}

struct JobRow: View {
    let job: Job
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(job.filename).font(.headline).lineLimit(1)
                Spacer()
                StatusBadge(status: job.status)
            }
            HStack(spacing: 10) {
                Label("\(job.copies)x", systemImage: "doc.on.doc")
                if job.duplex { Label("Duplex", systemImage: "doc.on.doc.fill") }
                Text(humanBytes(job.size_bytes))
            }
            .font(.caption)
            .foregroundStyle(.secondary)

            HStack {
                Text(job.createdDate.formatted(date: .abbreviated, time: .shortened))
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                Spacer()
                if let u = job.username {
                    Text(u).font(.caption2).foregroundStyle(.tertiary)
                }
            }
        }
        .padding(.vertical, 4)
    }

    private func humanBytes(_ n: Int) -> String {
        ByteCountFormatter.string(fromByteCount: Int64(n), countStyle: .file)
    }
}

struct StatusBadge: View {
    let status: String
    var body: some View {
        let (color, label): (Color, String)
        switch status {
        case "done": (color, label) = (.green, "Done")
        // Office file being turned into a PDF on the server; it has not reached
        // the printer yet, which is why it has no CUPS job id.
        case "converting": (color, label) = (.orange, "Converting…")
        case "pending": (color, label) = (.orange, "Pending")
        case "processing": (color, label) = (.blue, "Printing")
        case "error": (color, label) = (.red, "Failed")
        case "cancelled": (color, label) = (.gray, "Cancelled")
        default: (color, label) = (.primary, status.capitalized)
        }
        return Text(label)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(color.opacity(0.15), in: Capsule())
            .foregroundStyle(color)
    }
}

struct JobDetailView: View {
    @EnvironmentObject var api: APIClient
    let jobId: Int
    @State private var job: Job?
    @State private var err: String?
    @State private var showCancelAlert = false

    var body: some View {
        Group {
            if let job = job {
                List {
                    Section("Status") {
                        HStack { Text("State"); Spacer(); StatusBadge(status: job.status) }
                        if job.status == "converting" {
                            Text("Word, Excel and PowerPoint files are converted to PDF on the print server. This usually takes a few seconds, and up to a minute for a long document.")
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                        if let e = job.error {
                            Text(e).foregroundStyle(.red).font(.footnote)
                        }
                        if let p = job.printedDate {
                            HStack { Text("Printed"); Spacer(); Text(p.formatted(date: .abbreviated, time: .standard)) }
                                .font(.subheadline).foregroundStyle(.secondary)
                        }
                    }
                    Section("Document") {
                        LabeledContent("Filename", value: job.filename)
                        LabeledContent("Type", value: job.mime_type ?? "—")
                        LabeledContent("Size", value: ByteCountFormatter.string(fromByteCount: Int64(job.size_bytes), countStyle: .file))
                    }
                    Section("Options") {
                        LabeledContent("Copies", value: "\(job.copies)")
                        LabeledContent("Duplex", value: job.duplex ? "On" : "Off")
                        LabeledContent("Color", value: job.color_mode == "color" ? "Color" : "B&W")
                    }
                    Section("CUPS") {
                        LabeledContent("Job ID", value: job.cups_job_id.map(String.init) ?? "—")
                    }
                    if ["converting", "pending", "processing"].contains(job.status) {
                        Section {
                            Button("Cancel this job", role: .destructive) {
                                showCancelAlert = true
                            }
                        }
                    }
                }
            } else {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .navigationTitle("Job #\(jobId)")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    Task { await refresh() }
                } label: { Image(systemName: "arrow.clockwise") }
            }
        }
        .task {
            await refresh()
            await pollWhileConverting()
        }
        .alert("Cancel this print job?", isPresented: $showCancelAlert) {
            Button("Cancel job", role: .destructive) {
                Task {
                    do { try await api.cancelJob(jobId); await refresh() }
                    catch { err = error.localizedDescription }
                }
            }
            Button("Keep it", role: .cancel) {}
        }
    }

    private func refresh() async {
        do {
            job = try await api.refreshJob(jobId)
        } catch {
            err = error.localizedDescription
        }
    }

    /// Conversion finishes on its own, seconds to a minute after the upload,
    /// with nothing to push the news to the phone — so poll until the job
    /// leaves "converting". Every other status changes only when the printer
    /// does, and pull-to-refresh covers those. SwiftUI cancels this with the
    /// view.
    private func pollWhileConverting() async {
        while !Task.isCancelled && job?.status == "converting" {
            try? await Task.sleep(for: .seconds(3))
            if Task.isCancelled { return }
            await refresh()
        }
    }
}
