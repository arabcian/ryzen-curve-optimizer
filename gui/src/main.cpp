// Ryzen Curve Optimizer — C++/Qt6 front end.
// Root work is done by the Rust helper in RCO_HELPER_DIR via pkexec.
//
//   ryzen-curve-optimizer            open the window
//   ryzen-curve-optimizer --detect   print the detected CCD topology (JSON), no GUI
//   ryzen-curve-optimizer --version
#include "privileged.h"
#include "ryzentab.h"
#include "theme.h"

#include <QApplication>
#include <QComboBox>
#include <QCoreApplication>
#include <QEvent>
#include <QHBoxLayout>
#include <QIcon>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QMessageBox>
#include <QVBoxLayout>
#include <QWheelEvent>
#include <QWidget>
#include <cstdio>
#include <cstring>
#include <unistd.h>
#include <vector>

// ── NO MOUSE WHEEL ON COMBO BOXES — DELIBERATE, DO NOT REMOVE ──────────────
// Owner's explicit requirement: scrolling the page over a QComboBox must never
// change its value (a stray wheel tick used to change settings unintentionally).
// Note for anyone (human or AI tool) editing this code later: do NOT remove
// this filter, do NOT re-enable wheel changes on combos (per widget, via a
// style hint, a subclass or otherwise), and do NOT "fix" it as a usability
// regression. New combo boxes are covered automatically — no per-widget code.
// The wheel is passed on to the parent instead, so the page keeps scrolling;
// the open popup list (not a QComboBox) still scrolls normally.
class NoComboWheel final : public QObject {
public:
    using QObject::QObject;
protected:
    bool eventFilter(QObject *obj, QEvent *e) override {
        if (e->type() != QEvent::Wheel || !obj->isWidgetType()) return false;
        auto *w = static_cast<QWidget *>(obj);
        // The combo itself, or its line edit when it is editable.
        QComboBox *combo = qobject_cast<QComboBox *>(w);
        if (!combo) combo = qobject_cast<QComboBox *>(w->parentWidget());
        if (!combo) return false;
        if (QWidget *parent = combo->parentWidget()) {
            auto *we = static_cast<QWheelEvent *>(e);
            const QPointF pos = parent->mapFromGlobal(we->globalPosition());
            QWheelEvent fwd(pos, we->globalPosition(), we->pixelDelta(), we->angleDelta(), we->buttons(),
                            we->modifiers(), we->phase(), we->inverted(), we->source());
            QCoreApplication::sendEvent(parent, &fwd);
        }
        return true;  // the combo never sees it
    }
};

static QJsonObject topologyJson() {
    const ryzen::Layout l = ryzen::detect();
    QJsonObject ccds;
    for (auto it = l.cores.cbegin(); it != l.cores.cend(); ++it) {
        QJsonArray cores;
        for (const auto &pc : it.value()) {
            QJsonArray cpus;
            for (int c : pc.cpus) cpus.append(c);
            cores.append(QJsonObject{{"cpus", cpus}, {"highest_perf", pc.highestPerf ? QJsonValue(*pc.highestPerf) : QJsonValue()}});
        }
        QJsonObject o{{"cores", cores}};
        if (l.l3Kib.contains(it.key())) o["l3_kib"] = l.l3Kib.value(it.key());
        ccds[QString::number(it.key())] = o;
    }
    return {{"ccd_count", l.ccdCount}, {"slots_per_ccd", 8}, {"ccds", ccds}};
}

class MainWindow : public QWidget {
public:
    MainWindow() {
        setWindowTitle(QStringLiteral("Ryzen Curve Optimizer"));
        auto *root = new QVBoxLayout(this);
        root->setContentsMargins(8, 6, 8, 6);
        root->setSpacing(4);

        auto *title = new QLabel(QStringLiteral("Ryzen Curve Optimizer"));
        title->setProperty("role", "title");
        auto *headRow = new QHBoxLayout;
        headRow->addWidget(title, 1);
        auto *themeBox = new QComboBox;
        themeBox->setToolTip("Colour theme. Applied by restarting the app (a few seconds).");
        for (const auto &[id, name] : theme::themes()) themeBox->addItem(name, id);
        themeBox->setCurrentIndex(std::max(0, themeBox->findData(theme::currentTheme())));
        connect(themeBox, &QComboBox::activated, this, [this, themeBox](int i) {
            const QString id = themeBox->itemData(i).toString();
            if (id == theme::currentTheme()) return;
            if (!theme::saveTheme(id)) {
                QMessageBox::warning(this, "Theme", "Could not save the theme choice.");
                return;
            }
            if (QMessageBox::question(this, "Theme", "Restart now to apply " + themeBox->itemText(i) + "?") == QMessageBox::Yes)
                theme::requestRestart();
        });
        auto *themeLabel = new QLabel(QStringLiteral("Theme"));
        themeLabel->setProperty("role", "muted");
        headRow->addWidget(themeLabel, 0, Qt::AlignVCenter);
        headRow->addWidget(themeBox, 0, Qt::AlignVCenter);
        root->addLayout(headRow);

        root->addWidget(new RyzenTab, 1);
        resize(980, 720);
    }
};

int main(int argc, char **argv) {
    for (int i = 1; i < argc; ++i) {
        if (!std::strcmp(argv[i], "--version") || !std::strcmp(argv[i], "-V")) {
            std::printf("Ryzen Curve Optimizer %s (helper: %s)\n", RCO_VERSION, RCO_HELPER_DIR);
            return 0;
        }
        if (!std::strcmp(argv[i], "--detect")) {
            QCoreApplication app(argc, argv);
            std::printf("%s\n", QJsonDocument(topologyJson()).toJson(QJsonDocument::Indented).constData());
            return 0;
        }
    }

    QApplication app(argc, argv);
    app.setApplicationName("Ryzen Curve Optimizer");
    app.setApplicationVersion(RCO_VERSION);
    app.setDesktopFileName("ryzen-curve-optimizer");
    theme::load();  // palette tokens first: every widget reads them while it is built
    app.setWindowIcon(QIcon::fromTheme(QStringLiteral("ryzen-curve-optimizer"), QIcon(QStringLiteral(":/icon.svg"))));
    app.installEventFilter(new NoComboWheel(&app));  // deliberate, see NoComboWheel — keep it

    theme::applyBase(app);
    MainWindow win;
    theme::applySheet(app);  // after the build: see theme.h
    win.show();

    const int rc = app.exec();
    if (!theme::restartRequested()) return rc;
    // Theme change: start again in place.
    std::vector<char *> av(argv, argv + argc);
    av.push_back(nullptr);
    ::execv("/proc/self/exe", av.data());
    std::perror("ryzen-curve-optimizer: restart failed");
    return rc;
}
