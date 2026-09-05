use crate::ctl::Status;
use crate::Ev;
use std::sync::mpsc::Sender;
use tray_item::{IconSource, TrayItem};

const ICON_ARGB: &[u8] = include_bytes!("../resources/icon_argb.bin");

fn send(tx: &Sender<Ev>, ev: Ev) {
    let _ = tx.send(ev);
}

pub struct Tray {
    item: TrayItem,
    standalone_id: u32,
    session_id: u32,
}

impl Tray {
    pub fn build(tx: Sender<Ev>, status: &Status) -> Result<Self, String> {
        let mut item = TrayItem::new(
            "ResoNote",
            IconSource::Data {
                width: 36,
                height: 36,
                data: ICON_ARGB.to_vec(),
            },
        )
        .map_err(|e| format!("tray icon unavailable: {e}"))?;

        let toggle_id = item
            .inner_mut()
            .add_menu_item_with_id("Start Recording", {
                let tx = tx.clone();
                move || send(&tx, Ev::Toggle)
            })
            .map_err(|e| format!("tray toggle item failed: {e}"))?;

        let session_id = item
            .inner_mut()
            .add_menu_item_with_id("New / Load Session", {
                let tx = tx.clone();
                move || send(&tx, Ev::Session)
            })
            .map_err(|e| format!("tray session item failed: {e}"))?;

        let _ = item.add_menu_item("Open Notes Folder", {
            let tx = tx.clone();
            move || send(&tx, Ev::OpenNotes)
        });

        let _ = item.inner_mut().add_separator();
        let _ = item.add_menu_item("Quit", move || send(&tx, Ev::Quit));

        let mut tray = Self { item, standalone_id: toggle_id, session_id };
        tray.refresh(status);
        Ok(tray)
    }

    pub fn refresh(&mut self, status: &Status) {
        let recording = status.mode().is_some();
        let (standalone_label, session_label) = if recording {
            ("Stop Recording", "Stop Session")
        } else {
            ("Start Recording", "New / Load Session")
        };
        if let Err(e) = self
            .item
            .inner_mut()
            .set_menu_item_label(standalone_label, self.standalone_id)
        {
            log::warn!("tray label update failed: {e}");
        }
        if let Err(e) = self
            .item
            .inner_mut()
            .set_menu_item_label(session_label, self.session_id)
        {
            log::warn!("tray label update failed: {e}");
        }
    }
}