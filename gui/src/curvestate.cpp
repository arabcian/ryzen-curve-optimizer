#include "curvestate.h"

#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>

#include <algorithm>

namespace curvestate {

static const char *JOURNAL = "/var/lib/ryzen-curve-optimizer/oc-journal.jsonl";

static QString bootId() {
    QFile f(QStringLiteral("/proc/sys/kernel/random/boot_id"));
    return f.open(QIODevice::ReadOnly) ? QString::fromLatin1(f.readAll()).trimmed() : QString();
}

QString current(const QString &group) {
    QString state = QStringLiteral("stock");
    if (group != QLatin1String("cpu")) return state;
    QFile f(QString::fromLatin1(JOURNAL));
    if (!f.open(QIODevice::ReadOnly) || f.size() > (1 << 20)) return state;
    struct Rec { qint64 t; QString state; };
    QList<Rec> recs;
    const QString boot = bootId();
    for (const QByteArray &line : f.readAll().split('\n')) {
        const QJsonObject o = QJsonDocument::fromJson(line).object();
        if (o.isEmpty() || o.value("boot").toString() != boot || o.value("kind").toString() == QLatin1String("gpu_curve")) continue;
        QString st = o.value("state").toString();
        if (st == QLatin1String("reset")) st = QStringLiteral("stock");
        recs.append({o.value("t").toInteger(), st});
    }
    std::stable_sort(recs.begin(), recs.end(), [](const Rec &a, const Rec &b) { return a.t < b.t; });
    if (!recs.isEmpty()) state = recs.last().state;
    return state;
}

}  // namespace curvestate
