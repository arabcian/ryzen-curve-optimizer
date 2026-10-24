#pragma once
// Small sysfs/procfs readers. procfs/sysfs files report size 0, so QFile::atEnd()-style loops
// see them as empty; this is one open() and read() loop on a plain descriptor.
#include <QByteArray>
#include <QFile>
#include <QString>
#include <cerrno>
#include <fcntl.h>
#include <optional>
#include <unistd.h>

namespace sysfs {

inline std::optional<QByteArray> readRaw(const QString &path, qsizetype cap = 256 * 1024) {
    const int fd = ::open(QFile::encodeName(path).constData(), O_RDONLY | O_CLOEXEC | O_NOCTTY);
    if (fd < 0) return std::nullopt;
    QByteArray out;
    char buf[4096];
    for (;;) {
        const ssize_t n = ::read(fd, buf, sizeof buf);
        if (n < 0) {
            if (errno == EINTR) continue;
            out.clear();
            break;
        }
        if (n == 0) break;
        out.append(buf, n);
        if (out.size() >= cap) break;
    }
    ::close(fd);
    return out;
}

inline std::optional<QString> readText(const QString &path) {
    const auto raw = readRaw(path);
    if (!raw) return std::nullopt;
    return QString::fromUtf8(*raw).trimmed();
}

/// The "model name" of the first processor block of /proc/cpuinfo.
inline std::optional<QString> cpuModel() {
    const auto raw = readRaw(QStringLiteral("/proc/cpuinfo"));
    if (!raw) return std::nullopt;
    for (const QByteArray &line : raw->split('\n'))
        if (line.startsWith("model name")) return QString::fromUtf8(line.mid(line.indexOf(':') + 1)).trimmed();
    return std::nullopt;
}

} // namespace sysfs
