#pragma once
#include "cpustress.h"

#include <QElapsedTimer>
#include <QJsonObject>
#include <QWidget>
#include <functional>

class QCheckBox;
class QComboBox;
class QLabel;
class QProcess;
class QPushButton;
class QSpinBox;
class QTimer;
class QTreeWidget;

class CpuStressBox : public QWidget {
    Q_OBJECT
public:
    explicit CpuStressBox(std::function<bool()> canStart, QWidget *parent = nullptr);

Q_SIGNALS:
    void runningChanged(bool running);
    void alert(const QString &title, const QString &message);

private:
    void start();
    void launch();
    void readOutput();
    void finished(int exitCode, bool crashed);
    QJsonObject record(const QString &verdict) const;
    void tick();
    void fill();
    void fillDurations();
    void setIdle(bool idle);
    void note(const QString &text, const char *color);
    QString cpuName(int cpu) const;
    QString cpuList(const QList<int> &cpus) const;
    int totalSeconds() const;

    std::function<bool()> canStart_;
    QString program_;
    cpustress::Info info_;
    QComboBox *mode_ = nullptr, *load_ = nullptr, *duration_ = nullptr;
    QSpinBox *cycles_ = nullptr;
    QLabel *cyclesLabel_ = nullptr, *durationLabel_ = nullptr, *status_ = nullptr, *klogNote_ = nullptr;
    QCheckBox *transitions_ = nullptr;
    QPushButton *run_ = nullptr;
    QTreeWidget *results_ = nullptr;
    QProcess *proc_ = nullptr;
    QTimer *ticker_ = nullptr;
    QElapsedTimer elapsed_;
    QByteArray partial_;
    cpustress::Run run_state_;
    cpustress::Plan plan_;
    cpustress::Mode runMode_ = cpustress::Mode::PerCore;
    QString load_name_, curve_, profileHash_;
    qint64 id_ = 0;
    int seconds_ = 0, cycles_n_ = 1;
    bool cancelled_ = false;
};
