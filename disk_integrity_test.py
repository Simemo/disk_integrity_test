# Author: Maciej Cisek
# This small program generates a list of files of a specified size within a given path, and then tests reading them.
# The program was written for testing floppy disks and SD memory cards.

"""Generate files to a target size, then verify every file with SHA-256."""

import argparse
import errno
import hashlib
import os
import re
import shutil
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

READ_SIZE = 1024 * 1024
DEFAULT_FILE_SIZE = 64 * 1024
SIZE_UNITS = {"": 1, "k": 1_000, "m": 1_000_000, "g": 1_000_000_000}


def parse_size(value: str) -> int:
    """Parse a byte count with an optional decimal k, m, or g suffix."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmg]?)\s*", value.lower())
    if match is None:
        raise argparse.ArgumentTypeError(
            "podaj liczbę bajtów lub liczbę z przyrostkiem k, m albo g, np. 256k"
        )

    size = int(Decimal(match.group(1)) * SIZE_UNITS[match.group(2)])
    if size < 1:
        raise argparse.ArgumentTypeError("rozmiar musi wynosić co najmniej 1 bajt")
    return size


def format_size(size: int) -> str:
    return f"{size:,} B".replace(",", " ")


def generate_files(directory: Path, total_size: int, file_size: int):
    records = []
    generation_error = None
    remaining = total_size
    index = 1

    while remaining:
        current_size = min(file_size, remaining)
        path = directory / f"data_{index:06d}.bin"
        digest = hashlib.sha256()

        try:
            with path.open("xb") as file:
                bytes_left = current_size
                while bytes_left:
                    data = os.urandom(min(READ_SIZE, bytes_left))
                    written = file.write(data)
                    if written != len(data):
                        raise OSError(errno.ENOSPC, f"niepełny zapis pliku {path.name}")
                    digest.update(data)
                    bytes_left -= written
        except OSError as error:
            try:
                path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                print(f"Nie udało się usunąć niepełnego pliku {path}: {cleanup_error}")
            generation_error = (path.name, error)
            break

        records.append((path, current_size, digest.hexdigest()))
        remaining -= current_size
        print(f"Utworzono: {path.name} ({format_size(current_size)})")
        index += 1

    return records, generation_error


def verify_files(records):
    passed = 0
    failed = 0

    for path, expected_size, expected_digest in records:
        digest = hashlib.sha256()
        bytes_read = 0

        try:
            with path.open("rb") as file:
                while chunk := file.read(READ_SIZE):
                    digest.update(chunk)
                    bytes_read += len(chunk)

            if bytes_read == expected_size and digest.hexdigest() == expected_digest:
                print(f"OK: {path.name} ({format_size(bytes_read)})")
                passed += 1
            else:
                print(
                    f"BŁĄD: {path.name} — oczekiwano {format_size(expected_size)}, "
                    f"odczytano {format_size(bytes_read)}; suma SHA-256 nie zgadza się."
                )
                failed += 1
        except OSError as error:
            print(f"BŁĄD: {path.name} — nie udało się odczytać: {error}")
            failed += 1

    return passed, failed


def delete_generated_files(records, directory: Path) -> tuple[int, int, bool]:
    if not records:
        print("Brak wygenerowanych plików do usunięcia.")
        return 0, 0, False

    try:
        answer = input(
            f"Czy na pewno usunąć {len(records)} wygenerowanych plików? [t/N]: "
        )
    except EOFError:
        print("\nBrak potwierdzenia — pliki pozostają na dysku.")
        return 0, 0, False

    if answer.strip().lower() not in {"t", "tak", "y", "yes"}:
        print("Anulowano usuwanie — pliki pozostają na dysku.")
        return 0, 0, False

    deleted = 0
    errors = 0
    for path, _, _ in records:
        try:
            path.unlink()
            deleted += 1
            print(f"Usunięto: {path.name}")
        except OSError as error:
            errors += 1
            print(f"BŁĄD usuwania pliku {path.name}: {error}", file=sys.stderr)

    try:
        directory.rmdir()
    except OSError as error:
        errors += 1
        print(
            f"Nie udało się usunąć katalogu {directory} "
            f"(może zawierać inne pliki): {error}",
            file=sys.stderr,
        )

    return deleted, errors, True


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Tworzy pliki o zadanym łącznym rozmiarze i sprawdza ich odczyt "
            "za pomocą sum SHA-256. Jednostki k/m/g są dziesiętne (1000 B)."
        ),
        epilog=(
            "Przykłady:\n"
            "  python disk_integrity_test.py --size 100m --path ./test-data\n"
            "  python disk_integrity_test.py --size 100m --path ./test-data -d\n"
            "  disk_integrity_test.exe --size 256k --path C:\\test-data\n"
            "Wymagane parametry: --size (rozmiar, np. 256k, 100m lub 2g) "
            "oraz --path (katalog docelowy)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-s", "--size", required=True, type=parse_size,
        help="łączny rozmiar plików, np. 256k, 100m lub 2g",
    )
    parser.add_argument(
        "-p", "--path", required=True, type=Path,
        help="katalog na pliki; zostanie w nim utworzony unikalny podkatalog",
    )
    parser.add_argument(
        "--file-size", type=parse_size, default=DEFAULT_FILE_SIZE,
        help="maksymalny rozmiar pojedynczego pliku (domyślnie 64 KiB)",
    )
    parser.add_argument(
        "-d", "--delete", action="store_true",
        help="po weryfikacji zapytaj o usunięcie wygenerowanych plików",
    )
    if len(sys.argv) == 1:
        parser.print_help()
        return 0

    args = parser.parse_args()

    try:
        args.path.mkdir(parents=True, exist_ok=True)
        if not args.path.is_dir():
            print(f"Błąd: ścieżka nie jest katalogiem: {args.path}", file=sys.stderr)
            return 1

        free_space = shutil.disk_usage(args.path).free
        run_directory = Path(
            tempfile.mkdtemp(prefix="disk-test-", dir=args.path)
        )
    except OSError as error:
        print(f"Błąd przygotowania katalogu docelowego: {error}", file=sys.stderr)
        return 1

    print(f"Katalog testu: {run_directory}")
    print(f"Zadany rozmiar: {format_size(args.size)}")
    print("\nEtap 1/2: generowanie plików")
    records, generation_error = generate_files(
        run_directory, args.size, args.file_size
    )

    generated_size = sum(record[1] for record in records)
    if generated_size == args.size:
        print(
            f"Utworzono wszystkie pliki: {len(records)}, "
            f"łączny rozmiar: {format_size(generated_size)}."
        )
    else:
        print(
            f"Generowanie niekompletne: {len(records)} plików, "
            f"zapisano {format_size(generated_size)} z {format_size(args.size)}."
        )

    print("\nEtap 2/2: odczyt i weryfikacja")
    passed, failed = verify_files(records)

    allocated_size = 0
    allocation_supported = hasattr(os.stat_result, "st_blocks")
    if allocation_supported:
        for path, _, _ in records:
            try:
                allocated_size += path.stat().st_blocks * 512
            except OSError:
                allocation_supported = False
                break

    deleted = 0
    deletion_errors = 0
    deletion_confirmed = False
    if args.delete:
        deleted, deletion_errors, deletion_confirmed = delete_generated_files(
            records, run_directory
        )

    print("\nPodsumowanie")
    print(f"Katalog z plikami: {run_directory}")
    print(f"Żądany rozmiar: {format_size(args.size)}")
    print(f"Zapisany rozmiar: {format_size(generated_size)}")
    print(f"Wolne miejsce przed generowaniem: {format_size(free_space)}")
    print(f"Liczba utworzonych plików: {len(records)}")
    print() 
    print(f"Odczytane poprawnie: {passed}")
    print(f"Błędy odczytu/weryfikacji: {failed}")
    print() 
    space_error = generation_error is not None and generation_error[1].errno in {
        errno.ENOSPC,
        getattr(errno, "EDQUOT", errno.ENOSPC),
    }
    if args.size > free_space or space_error:
        print(
            "UWAGA: dostępna przestrzeń była za mała. "
            f"Wymagane: {format_size(args.size)}, "
            f"wolne przed generowaniem: {format_size(free_space)}, "
            f"wygenerowano: {format_size(generated_size)}."
        )
    if generation_error is not None:
        file_name, error = generation_error
        print(f"Zatrzymano generowanie przy pliku {file_name}: {error}")
    if allocation_supported:
        allocation_label = (
            "Zajęte miejsce przed usunięciem"
            if deletion_confirmed
            else "Zajęte miejsce według systemu plików"
        )
        print(f"{allocation_label}: {format_size(allocated_size)}")
    if deletion_confirmed:
        print(f"Usunięte pliki: {deleted} z {len(records)}")
        if deletion_errors:
            print(f"Błędy usuwania: {deletion_errors}")
    elif records:
        print("Pliki pozostają na dysku; usuń podany katalog, aby zwolnić miejsce.")

    return (
        0
        if generated_size == args.size and failed == 0 and deletion_errors == 0
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
