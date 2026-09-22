// Keeps a console window from appearing behind the app in a release build.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    voxvault_app_lib::run()
}
