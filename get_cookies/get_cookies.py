import abc
from collections.abc import Generator
from dataclasses import dataclass
from enum import auto
import os
import pathlib
import shutil
import sqlite3
import sys
import argparse
import tempfile
from typing import Literal, get_args, override

"""Netscape header: used in output file"""
COOKIE_HEADERS = [
    "# Netscape HTTP Cookie File",
    "# http://www.netscape.com/newsref/std/cookie_spec.html",
    "# This is a generated file!  Do not edit.",
]

# BrowserType = Literal["Firefox", "Chrome", "Brave", "Edge"] # next
"""Implemented Browsers: used for command line argument validation"""
BrowserType = Literal["Firefox"]
IMPLEMENTED_BROWSERS = list(get_args(BrowserType))


class Browser(abc.ABC):

    def __init__(self, browser_path: pathlib.Path) -> None:
        """The browser path will be set, where the cookie database is somewhere stored

        Args:
            browser_path (pathlib.Path): The base path of the browser ( e.g., ~/.mozilla/firefox )
        """
        self.browser_path: pathlib.Path = browser_path

    @abc.abstractmethod
    def _find_sqlite(self, profile_path: pathlib.Path) -> pathlib.Path | None:
        """Finds the cookie file for the corresponding Browser

        Args:
            profile_path (pathlib.Path): The directory in which the sqlite database will be searched.

        Returns:
            pathlib.Path | None: When the file is found the path is returned otherwise None
        """
        pass

    @abc.abstractmethod
    def get_sqlite_from_user_profile(self) -> Generator[pathlib.Path | None]:
        """Uses `self.browser_path` to look in lower directories for the cookie database

        Returns:
            Generator[pathlib.Path | None]: A browser could have multiple
                profiles. Therefore multiple cookie databases could be found. Not every
                directory has a cookie database, therefore None can also be generated
                and must be filtered out later"""
        pass

    def get_cookie_list(self) -> list[pathlib.Path]:
        """Scouts the browser directory and returns all possible cookie DB paths.

        Returns:
            list[pathlib.Path]: The list of all cookie databases with the None
                elements filtered out"""
        return [
            sql_file
            for sql_file in self.get_sqlite_from_user_profile()
            if sql_file is not None
        ]

    @abc.abstractmethod
    def extract_cookies(self, db_path: pathlib.Path) -> list[Cookie]:
        """Extracts cookies from the browser sqlite database.

        Args:
            db_path (pathlib.Path): Each browser must implement their method on
                how to extract their cookies"""
        pass


class Firefox(Browser):

    @override
    def _find_sqlite(self, profile_path: pathlib.Path) -> pathlib.Path | None:
        cookie_file = profile_path / "cookies.sqlite"

        if cookie_file.is_file():
            return cookie_file

        return None

    @override
    def get_sqlite_from_user_profile(self) -> Generator[pathlib.Path | None]:
        """If browser_path points directly at a profile dir, check it first"""
        direct = self._find_sqlite(self.browser_path)
        if direct:
            yield direct
            return

        """Otherwise treat it as the parent containing multiple profiles"""
        for path in self.browser_path.glob("*.*"):
            file = self._find_sqlite(path)
            if file:
                yield file

    @override
    def extract_cookies(self, db_path: pathlib.Path) -> list[Cookie]:
        cookies: list[Cookie] = []
        conn = None
        cursor = None
        tmp_path = None

        try:
            with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
                tmp_path = pathlib.Path(tmp.name)

            _ = shutil.copy2(db_path, tmp_path)

            for suffix in (".sqlite-wal", ".sqlite-shm"):
                sidecar = db_path.with_suffix(suffix)
                if sidecar.exists():
                    _ = shutil.copy2(sidecar, tmp_path.with_suffix(suffix))

            conn = sqlite3.connect(tmp_path, timeout=10)
            cursor = conn.cursor()
            _ = cursor.execute(
                "SELECT host, path, isSecure, expiry, name, value FROM moz_cookies WHERE host LIKE '%.youtube%';"
            )

            for row in cursor.fetchall():
                host, path, is_secure, expiry, name, value = row
                cookies.append(
                    Cookie(
                        host=host,
                        path=path,
                        is_secure=bool(is_secure),
                        expiry=expiry,
                        name=name,
                        value=value,
                    )
                )

            return cookies

        except sqlite3.OperationalError as e:
            if "unable to open" in str(e):
                print("Could not open the database file. Check file permissions.", file=sys.stderr)
            else:
                print("Database is locked. Please close your browser and try again.", file=sys.stderr)
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(-1)

        except sqlite3.Error as e:
            print(f"SQLite error: {e}", file=sys.stderr)
            sys.exit(-1)

        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
            if tmp_path:
                for suffix in ("", "-wal", "-shm"):
                    f = tmp_path.with_suffix(".sqlite" + suffix)
                    f.unlink(missing_ok=True)


"""Registered browsers: Objects are created with literal index in make_config"""
BROWSER_REGISTRY: dict[BrowserType, type[Browser]] = {"Firefox": Firefox}


@dataclass
class Cookie:
    """Represents a single HTTP cookie with Netscape-compatible attributes.

    Attributes:
        host (str): The domain that created and can read the cookie.
        path (str): The path on the server for which the cookie is valid.
        is_secure (bool): Whether the cookie requires a secure HTTPS connection.
        expiry (int): The UNIX timestamp when the cookie expires.
        name (str): The name of the cookie.
        value (str): The value stored in the cookie."""

    host: str
    path: str
    is_secure: bool
    expiry: int
    name: str
    value: str

    @property
    def domain_flag(self) -> str:
        """Determines the Netscape domain flag.

        Returns:
            str: "TRUE" if the host starts with a dot (subdomains allowed),
                otherwise "FALSE"."""
        return "TRUE" if self.host.startswith(".") else "FALSE"

    @property
    def secure_flag(self) -> str:
        """Determines the Netscape secure flag.

        Returns:
            str: "TRUE" if the cookie is secure, otherwise "FALSE"."""
        return "TRUE" if self.is_secure else "FALSE"

    def to_netscape_string(self) -> str:
        """Formats the cookie to a single Netscape HTTP cookie line.

        Returns:
            str: A tab-separated string formatted for Netscape cookie files."""
        return f"{self.host}\t{self.domain_flag}\t{self.path}\t{self.secure_flag}\t{self.expiry}\t{self.name}\t{self.value}\n"


def write_netscape_cookies(cookies: list[Cookie], output_path: pathlib.Path) -> None:
    """Writes a list of Cookie objects to the Netscape format.

    Args:
        cookies (list[Cookie]): The list of cookies to be written.
        output_path (pathlib.Path): The file path where the cookies will be saved."""
    try:
        with open(output_path, "w") as f:
            _ = f.write("\n".join(COOKIE_HEADERS) + "\n\n")

            for cookie in cookies:
                _ = f.write(cookie.to_netscape_string())

    except IOError as e:
        print(f"Error writing to file: {e}", file=sys.stderr)
        sys.exit(-1)


class CliArgs(argparse.Namespace):
    """Container for command-line arguments.

    Attributes:
        browser_path (pathlib.Path | None): Custom path to browser profile.
        browser (BrowserType | None): The name of the browser to target.
        output_path (pathlib.Path | None): Custom output file path.
        verbose (bool): Whether to enable detailed logging.
        auto (bool): Whether to auto-select without prompting."""
    browser_path: pathlib.Path | None  # pyright: ignore[reportUninitializedInstanceVariable]
    browser: str | None               # pyright: ignore[reportUninitializedInstanceVariable]
    output_path: pathlib.Path | None  # pyright: ignore[reportUninitializedInstanceVariable]
    verbose: bool                     # pyright: ignore[reportUninitializedInstanceVariable]
    auto: bool                        # pyright: ignore[reportUninitializedInstanceVariable]


@dataclass
class AppConfig:
    """Finalized application configuration after validation.

    Attributes:
        browser (Browser): An instance of a Browser-based class.
        output_path (pathlib.Path): Validated path for the output file.
        verbose (bool): Verbosity toggle."""

    browser: Browser
    output_path: pathlib.Path
    verbose: bool
    auto: bool

    def __post_init__(self):
        valid: bool
        err: str | None
        valid, err = validate_config(self)

        if not valid:
            print(err, file=sys.stderr)
            sys.exit(-1)


def make_config(args: CliArgs) -> AppConfig:
    """Orchestrates the creation of the application configuration.

    It resolves paths (expanding ~), selects the correct Browser class
    from the registry, and determines the final output file destination.

    Args:
        args (CliArgs): The raw arguments parsed from the command line.

    Returns:
        AppConfig: A validated configuration object ready for use."""
    if args.browser_path is not None:
        final_browser_path = args.browser_path.expanduser()

    else:
        final_browser_path = pathlib.Path("~/.mozilla/firefox/").expanduser()

    if args.browser is not None:
        browser_class = BROWSER_REGISTRY.get(args.browser)

        if browser_class is None:
            print(
                f'\nBrowser "{args.browser}" was not registered!\n\n>\tThis feature has to be implemented!\n',
                file=sys.stderr,
            )
            sys.exit(-1)

    else:
        browser_class = BROWSER_REGISTRY["Firefox"]

    if args.output_path is None:
        final_output_path = pathlib.Path(__file__).parent.resolve() / "../data/cookies.txt"
        final_output_path = final_output_path.resolve()

    else:
        db_path = args.output_path.expanduser()

        if db_path.is_dir():
            final_output_path = db_path / "cookies.txt"

        else:
            final_output_path = db_path

    return AppConfig(
        browser=browser_class(final_browser_path),
        output_path=final_output_path,
        verbose=args.verbose,
        auto=args.auto,
    )


def validate_config(config: AppConfig) -> tuple[bool, str | None]:
    """Checks the AppConfig for logical errors or missing filesystem paths.

    Args:
        config (AppConfig): The configuration object to validate.

    Returns:
        tuple[bool, str | None]: A tuple containing a success boolean and
            an optional error message if validation failed."""
    if type(config.browser) not in BROWSER_REGISTRY.values():
        return (
            False,
            f"Browser must be one of {IMPLEMENTED_BROWSERS} but is: {config.browser}",
        )

    if type(config.verbose) is not bool:
        return False, f"verbose must be of type bool but is: {type(config.verbose)}"

    if not config.output_path.parent.exists():
        return False, f"Output directory does not exist: {config.output_path.parent}"

    return True, None


def user_select_one(sqlite_files: list[pathlib.Path], auto: bool) -> pathlib.Path:
    """Prompts the user to select a single database file if multiple are found.

    The list is sorted by modification time (most recent first) to
    help the user identify their active profile.

    Args:
        sqlite_files (list[pathlib.Path]): A list of paths to potential
            cookie databases.

        auto (bool): automatically pick the most recent used DB

    Returns:
        pathlib.Path: The chosen database file path."""
    if len(sqlite_files) == 1:
        return sqlite_files[0]

    if len(sqlite_files) == 0:
        print("failed to get sqlite file!", file=sys.stderr)
        sys.exit(-1)

    sqlite_files_ordered = sorted(
        sqlite_files, key=lambda x: x.stat().st_mtime, reverse=True
    )

    if auto:
        print(f"Auto-selected most recent database: {sqlite_files_ordered[0]}")
        return sqlite_files_ordered[0]

    print("Choose the database to extract the cookies from!\n")

    while True:
        for i, s in tuple(enumerate(sqlite_files_ordered))[::-1]:
            print(f"\t[{i+1}] - {s}")

        choice_human = input("\nSelect the database (default: 1): ")

        if choice_human == "":
            return sqlite_files_ordered[0]

        try:
            choice_human_num = int(choice_human)

        except Exception as e:
            print("Please just enter numbers!", file=sys.stderr)
            continue

        if choice_human_num <= 0 or choice_human_num > len(sqlite_files_ordered):
            print("Please enter a given index!", file=sys.stderr)
            continue

        return sqlite_files_ordered[choice_human_num - 1]


def main(conf: AppConfig):
    """Main execution flow for the cookie extraction process.

    Checks for platform compatibility, finds the database, extracts
    the cookies, and writes them to the output file.

    Args:
        conf (AppConfig): The validated application configuration.

    Returns:
        int: 0 on success, -1 on failure."""
    if not sys.platform.startswith("linux"):
        print("Error: Platform not supported as of now!", file=sys.stderr)
        print(
            "\tplease use a browser extensions to get the cookies in netscape format", file=sys.stderr
        )
        return -1

    cookie_list = conf.browser.get_cookie_list()
    print(cookie_list)
    sqlite_to_parse: pathlib.Path = user_select_one(cookie_list, conf.auto)
    cookies = conf.browser.extract_cookies(sqlite_to_parse)

    if conf.verbose:
        print(f"Extracted {len(cookies)} cookies.")

    write_netscape_cookies(cookies, conf.output_path)

    print(f"[+] SUCCESS: written netscape cookie format to: {conf.output_path}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog=sys.argv[0],
        description="Script to get firefox cookies in netscape format",
        epilog="YMDE-project cookie extractor",
    )

    _ = parser.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose output"
    )

    _ = parser.add_argument(
        "-p",
        "--browser-path",
        default=os.environ.get("COOKIE_EXTRACTOR_BROWSER_PATH"),
        type=pathlib.Path,
        help="If the browser path is not found you need to provide the path ( e.g., ~/.mozilla/firefox )",
        required=False,
    )

    _ = parser.add_argument(
        "-b",
        "--browser",
        default=os.environ.get("COOKIE_EXTRACTOR_BROWSER_PROVIDER"),
        choices=IMPLEMENTED_BROWSERS,
        help="The browser with cookies to extract. ( e.g., Firefox, Chrome, Brave, ... )",
        required=False,
    )

    _ = parser.add_argument(
        "-o",
        "--output-path",
        default=os.environ.get("COOKIES"),
        type=pathlib.Path,
        help="Path to output directory or specific .txt file",
        required=False,
    )

    _ = parser.add_argument(
        "-a",
        "--auto",
        default=os.environ.get("AUTO_EXTRACT_COOKIES", "").lower() in ("1", "true"),
        action="store_true",
        help="Automatically select the most recent database without prompting.",
    )
    args_config = parser.parse_args(namespace=CliArgs())
    print(args_config)
    conf = make_config(args_config)
    _ = main(conf)
