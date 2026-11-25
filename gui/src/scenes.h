#pragma once
// What CO Search asks of a scene engine, answered for an app that has none: no scenes, no game sessions,
// no active scene. Only the AC state is real. (Centurion's scene engine gives richer answers.)
#include <QDir>
#include <QFile>
#include <QStandardPaths>
#include <QString>
#include <QStringList>
#include <optional>

namespace scenes {

struct Choice {
    enum Kind { Unchanged, Reset, Profile } kind = Unchanged;
    QString name;
    bool operator==(const Choice &) const = default;
};
struct Scene { QString name; Choice cpu; };

/// Directory the app keeps per-user state in; CO Search stores co-search.json beside it.
inline QString dir() {
    return QStandardPaths::writableLocation(QStandardPaths::GenericConfigLocation) + QStringLiteral("/ryzen-curve-optimizer/scenes");
}
inline QStringList names() { return {}; }
inline std::optional<Scene> load(const QString &) { return std::nullopt; }
inline QString outcomeLabel(const QString &state) { return state; }
inline QString activeScene() { return {}; }
inline int gameSessions() { return 0; }

/// false = on battery, true = on mains, nullopt = unknown (desktop, no power supply information).
inline std::optional<bool> onAc(bool = false) {
    std::optional<bool> seen;
    const QDir ps(QStringLiteral("/sys/class/power_supply"));
    for (const QString &e : ps.entryList(QDir::Dirs | QDir::NoDotAndDotDot | QDir::System)) {
        QFile type(ps.filePath(e) + QStringLiteral("/type"));
        if (!type.open(QIODevice::ReadOnly) || type.read(16).trimmed() != "Mains") continue;
        QFile online(ps.filePath(e) + QStringLiteral("/online"));
        if (!online.open(QIODevice::ReadOnly)) continue;
        if (online.read(4).trimmed() == "1") return true;
        seen = false;
    }
    return seen;
}

}  // namespace scenes
