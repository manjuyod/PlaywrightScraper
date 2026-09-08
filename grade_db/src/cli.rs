use clap::{Parser, Subcommand};

#[derive(Debug, Parser)]
#[command(
    name = "grade-db",
    version,
    about = "Local CRM/grade-state database boundary"
)]
pub struct Cli {
    #[command(subcommand)]
    pub command: Command,
}

#[derive(Debug, Subcommand)]
pub enum Command {
    Job {
        #[command(subcommand)]
        command: JobCommand,
    },
    Result {
        #[command(subcommand)]
        command: ResultCommand,
    },
    Doctor,
}

#[derive(Debug, Subcommand)]
pub enum JobCommand {
    Start,
    Heartbeat,
    Complete,
    Fail,
}

#[derive(Debug, Subcommand)]
pub enum ResultCommand {
    Post,
}
