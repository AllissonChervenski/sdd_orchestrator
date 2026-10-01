from orchestrator.storage.sqlite import StateStore


def test_state_persistence(tmp_path):
    store=StateStore(tmp_path/"db.sqlite"); wid=store.create_workflow("feature")
    store.update_workflow(wid,"RED_VERIFY",{"attempts":{"red":1}},"T1")
    item=store.get_workflow(wid)
    assert item["stage"]=="RED_VERIFY" and item["state"]["attempts"]["red"]==1
