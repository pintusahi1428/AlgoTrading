from execution_monitor import ExecutionQualityMonitor


if __name__ == "__main__":
    report = ExecutionQualityMonitor().summary()
    print("=" * 72)
    print("MASTER SNIPER EXECUTION QUALITY")
    print("=" * 72)
    for key, value in report.items():
        print(f"{key:<24}: {value}")
    print("=" * 72)
