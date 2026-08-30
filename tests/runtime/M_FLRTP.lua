-- Disposable Task 10 probe identities.  Every value is synthetic.
FLDLVProbe = FLDLVProbe or {}
FLDLVProbe.config = {
    area_resref = "FLRTPRA",
    creature_script = "FLRTPU",
    duplicate_script = "FLRTPD",
    last_slot_script = "FLRTPL",
    full_script = "FLRTPF",
    container_script = "FLRTPC",
    pile_script = "FLRTPP",
    item_resrefs = {
        creature = "FLRTPIT",
        container = "FLRTPJ",
        pile = "FLRTPK",
        filler = "FLRTPFL",
    },
    charge_triples = {
        creature = { 2, 3, 5 },
        container = { 7, 11, 13 },
        pile = { 17, 19, 23 },
        filler = { 0, 0, 0 },
    },
    ability_maxima = {
        creature = { 7, 11, 13 },
        container = { 17, 19, 23 },
        pile = { 29, 31, 37 },
        filler = { 0, 0, 0 },
    },
    items = {
        creature = { resref = "FLRTPIT", charges = { 2, 3, 5 } },
        container = { resref = "FLRTPJ", charges = { 7, 11, 13 } },
        pile = { resref = "FLRTPK", charges = { 17, 19, 23 } },
    },
}
