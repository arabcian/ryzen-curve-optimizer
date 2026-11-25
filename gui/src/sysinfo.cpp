#include "sysinfo.h"

#include <QFile>

namespace sysinfo {

Opt dmi(const QString &field) {
    // Field names are fixed identifiers (bios_version, ...): never a path.
    if (field.isEmpty() || field.contains(QLatin1Char('/'))) return std::nullopt;
    QFile f(QStringLiteral("/sys/class/dmi/id/") + field);
    if (!f.open(QIODevice::ReadOnly)) return std::nullopt;
    const QString v = QString::fromUtf8(f.read(256)).trimmed();
    return v.isEmpty() ? std::nullopt : Opt(v);
}

Opt cpuModel() {
    QFile f(QStringLiteral("/proc/cpuinfo"));
    if (!f.open(QIODevice::ReadOnly)) return std::nullopt;
    const QByteArray head = f.read(8192);  // the first processor block is enough
    for (const QByteArray &line : head.split('\n')) {
        if (!line.startsWith("model name")) continue;
        const int colon = line.indexOf(':');
        if (colon < 0) continue;
        const QString v = QString::fromUtf8(line.mid(colon + 1)).trimmed();
        if (!v.isEmpty()) return v;
    }
    return std::nullopt;
}

}  // namespace sysinfo
