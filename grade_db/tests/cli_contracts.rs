use clap::Parser;
use grade_db::cli::{Cli, Command, JobCommand, ResultCommand};
use std::process::{Command as ProcessCommand, Stdio};

#[test]
fn documented_commands_have_stable_windows_friendly_names() {
    assert!(matches!(
        Cli::try_parse_from(["grade-db", "job", "start"])
            .unwrap()
            .command,
        Command::Job {
            command: JobCommand::Start
        }
    ));
    assert!(matches!(
        Cli::try_parse_from(["grade-db", "job", "heartbeat"])
            .unwrap()
            .command,
        Command::Job {
            command: JobCommand::Heartbeat
        }
    ));
    assert!(matches!(
        Cli::try_parse_from(["grade-db", "job", "complete"])
            .unwrap()
            .command,
        Command::Job {
            command: JobCommand::Complete
        }
    ));
    assert!(matches!(
        Cli::try_parse_from(["grade-db", "job", "fail"])
            .unwrap()
            .command,
        Command::Job {
            command: JobCommand::Fail
        }
    ));
    assert!(matches!(
        Cli::try_parse_from(["grade-db", "result", "post"])
            .unwrap()
            .command,
        Command::Result {
            command: ResultCommand::Post
        }
    ));
    assert!(matches!(
        Cli::try_parse_from(["grade-db", "doctor"]).unwrap().command,
        Command::Doctor
    ));
}

#[test]
fn arbitrary_sql_and_network_commands_do_not_exist() {
    for command in ["sql", "serve", "listen", "migrate"] {
        assert!(Cli::try_parse_from(["grade-db", command]).is_err());
    }
}

#[test]
fn invalid_stdin_returns_a_nonzero_exit_with_json_only_stdout() {
    let output = ProcessCommand::new(env!("CARGO_BIN_EXE_grade-db"))
        .args(["job", "start"])
        .stdin(Stdio::null())
        .output()
        .unwrap();

    assert!(!output.status.success());
    let response: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(response["ok"], false);
    assert_eq!(response["error"], "validation_error");
}
