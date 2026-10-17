// Duergar merchants (component 10210), from Item Randomiser's d/udduer01.d.
// A guest of the drow who does not look like one (DrowGuest 1,
// PlayerLooksLikeDrow 0) can buy from both of the merchant's stores, so items
// a randomiser places in either stay within reach: the original game offers
// only the special store (UDDUER02) then. Such a guest gets the greeting
// below instead of the original one (state 12); the "nothing new" answer is a
// copy of state 14 that also offers both stores. Everyone else, and state 14
// itself, stay as they are.

ADD_STATE_TRIGGER udduer01 12 ~Global("DrowGuest","GLOBAL",0)~

APPEND udduer01

  IF ~Global("PlayerLooksLikeDrow","GLOBAL",0) Global("DrowGuest","GLOBAL",1)~ drowguest
    SAY @0
    IF ~Global("flgoodstuff","LOCALS",0)~ DO ~SetGlobal("flgoodstuff","LOCALS",1)~ GOTO goodstuff0
    IF ~Global("flgoodstuff","LOCALS",1)~ GOTO goodstuff1
  END

  IF ~~ goodstuff0
    SAY @1
    = @2
    IF ~~ GOTO goodstuff1
  END

  IF ~~ goodstuff1
    SAY @3
    ++ #50226 + nonews
    ++ #50227 + 13
    ++ @4 DO ~StartStore("udduer01",LastTalkedToBy(Myself))~ EXIT
    ++ @5 DO ~StartStore("udduer02",LastTalkedToBy(Myself))~ EXIT
  END

  IF ~~ nonews
    SAY #50230
    ++ @6 DO ~StartStore("udduer01",LastTalkedToBy(Myself))~ EXIT
    ++ @7 DO ~StartStore("udduer02",LastTalkedToBy(Myself))~ EXIT
    ++ #50232 + 13
  END
END
