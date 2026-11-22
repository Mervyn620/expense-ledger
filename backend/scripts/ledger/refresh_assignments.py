from payment_assignments import refresh_assignment_file


def main():
    stats = refresh_assignment_file()
    print(
        "Refreshed payment assignments. "
        f"assignments={stats['assignments']} unassigned={stats['unassigned']}"
    )


if __name__ == "__main__":
    main()
