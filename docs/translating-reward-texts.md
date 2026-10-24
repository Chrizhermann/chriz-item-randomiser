# Translating CIR's reward texts

**Call for translators.** Chriz Item Randomiser's extended pools move the rewards of
some Shadows of Amn quests and of Aldeth's quest in Baldur's Gate. Where the quest's own
text names the reward ("Please, take this ring."), CIR shows Item Randomiser's reworded
line instead ("Please, accept this gift."). These 24 lines exist in English, Polish
(Item Randomiser's own translation) and German. In a game in any other language the
configurator leaves the quests that show them out of the extended pools, with the
reason ("Item Randomiser's reward texts have no Spanish translation yet"); the game
never shows the English line in another language. A translation of the 24 lines below
brings those quests back for that language.

BG:EE shows only Aldeth's two lines (@1649 and @1650), so Hungarian, Japanese and
Turkish (BG:EE only) need just those two. CIR wrote the German lines and Polish @1650
itself; Item Randomiser has the others.

## Languages

| Game language | Folder | Code page | Female wording |
| --- | --- | --- | --- |
| Czech (`cs_CZ`) | `languages/czech/` | Windows-1250 | not used |
| Spanish (`es_ES`) | `languages/spanish/` | Windows-1252 | used |
| French (`fr_FR`) | `languages/french/` | Windows-1252 | used |
| Hungarian (`hu_HU`, BG:EE) | `languages/hungarian/` | Windows-1250 | not used |
| Italian (`it_IT`) | `languages/italian/` | Windows-1252 | used |
| Japanese (`ja_JP`, BG:EE) | `languages/japanese/` | Shift JIS (Windows-932) | used |
| Korean (`ko_KR`) | `languages/korean/` | Windows-949 | not used |
| Brazilian Portuguese (`pt_BR`) | `languages/portuguese/` | Windows-1252 | used |
| Russian (`ru_RU`) | `languages/russian/` | Windows-1251 | used |
| Turkish (`tr_TR`, BG:EE) | `languages/turkish/` | Windows-1254 | not used |
| Ukrainian (`uk_UA`) | `languages/ukrainian/` | Windows-1251 | used |
| Chinese (`zh_CN`) | `languages/schinese/` | GBK (Windows-936) | not used |

The folder names are WeiDU's, and so are the code pages: when CIR is installed in a
language, WeiDU converts that folder to UTF-8 (the game's text encoding) and the
configurator reads it either way. Any other game language, such as Norwegian (`nb_NO`)
in older BG:EE releases, gets the reason "Item Randomiser's reward texts have no
translation for the game language nb_no" until a row is added to `LANGUAGES` in
`tools/flir_config/translations.py`.

## How to translate

1. Copy the template below into two files of your language's folder,
   `in-game.tra` and `ppdjinn.tra`, saved in its code page with Windows line ends (CRLF).
2. Replace each English text between the tildes and keep the `@` numbers.
3. Keep `<GABBER>` as it is: the game puts in the name of the party member who talks.
4. Where a female protagonist is addressed differently in your language, add the female
   wording as a second text on the same line, as WeiDU reads it:
   `@104 = ~... der bessere Kämpfer ...~ ~... die bessere Kämpferin ...~`.
   The game shows it to a female protagonist (`dialogF.tlk`). In a language marked "not
   used" above the game has one text for both, so the first one is enough.
5. Each line replaces one of the game's own strings (the numbers in the comments). Your
   game's own wording of that string, for example in Near Infinity's string table, shows
   the speaker, the tone and the names your translation uses (Imnesvale, the Storm Lord).
   Translate Item Randomiser's line, which speaks of "an item", not the game's string,
   which names the original reward.
6. @1650 replaces a journal entry. Write its text only: CIR puts the title line of the
   game's own entry above it, in the game's words.
7. Send the two files as a pull request or attach them to an issue at
   <https://github.com/Chrizhermann/chriz-item-randomiser>.

`languages/german/` is an example of a finished translation.

## Template

```
// languages/<folder>/in-game.tra
// replaces string 74025 (AMCLER01.DLG, a priest in Amkethran)
@100 = ~Please, accept this gift.~
// replaces string 38758 (KPGOL01.BCS, KPGOL02.BCS, KPGOL05.BCS, the De'Arnise Keep golems)
@101 = ~As you pick up the item, two of the golems come to life and attack.~
// replaces string 52664 (UDLESA.DLG, Lesa in the Underdark)
@104 = ~I yield. You have proven the better combatant this day. I... I yield the victory.~
// replaces string 43514 (TRGENI01.DLG, the genie in Trademeet)
@110 = ~Here, then, is an item that you may find useful.  It has been mine own for some time, but I shall be able to make do without it.  Use it well, wayfarer!~
// replaces string 29325 (SCTELWYN.DLG, a priest of Helm)
@111 = ~You have earned more than mere gold as well. Helm is practical, and would have his payment be so as well. You shall have this item, to further yourself.~
// replaces string 28489 (SCSAIN.DLG, a priest of Lathander)
@116 = ~I give you this blessed item, that you might prevail gainst what enemies you may have. You have served well under Lathander. Go in peace.~
// replaces string 29378 (SCYARRYL.DLG, a priest of Talos, the Storm Lord)
@117 = ~You have also earned a blessing in the form of this item. You will serve the Storm Lord all the better with it.~
// replaces string 40850 (DDWELL.BCS, the well's winch)
@399 = ~You begin to operate the winch, slowly lifting the bucket from the bottom of the well.  After a few moments the bucket is within reach.  You pull an item from the murky water in the bucket.~
// replaces strings 63681 and 64609 (GORCAMB.DLG, the cambion in Watcher's Keep)
@497 = ~Excellent.  I have access to a magical item and a Scroll of Wish, both rather impressive items.  And there is the gem, as well.  What would you wish to play for?~
// replaces strings 63683 and 64611 (GORCAMB.DLG)
@498 = ~I will play for the magical item.~
// replaces string 63719 (GORCAMB.DLG)
@499 = ~You desire the magical item, and here it is.  If you wish to play again, I'd appreciate a chance to gain my freedom, friend.~
// replaces string 33760 (JUGJER01.DLG, Jermien in Athkatla's Government District)
@698 = ~Hmm.  I seem to recall that I've got a magical item around here that I won in a bet from that fool, Tolgerias.  I've no use for it, but it's valuable.  Find me some mimic's blood and it's yours.~
// replaces string 33777 (JUGJER01.DLG)
@699 = ~I took the liberty of digging out that item, earlier, just in case.  Rather prophetic of me, don't you think?  It was just collecting dust, anyway...so here you go and well earned, I might add.~
// replaces string 32191 (UHMAY01.DLG, the mayor of Imnesvale)
@1393 = ~No, no.  The item, itself, is more than enough.~
// replaces string 32062 (UHMAY01.DLG)
@1394 = ~Herrrm...well, my village is in dire need.  I'll tell you what...if you end the murders, I will give you a magical item that belonged to our first ranger protector.~
// replaces string 32176 (UHMAY01.DLG)
@1395 = ~As agreed, here is the item of my ancestor ranger...I assure you it is most strongly bound with magic!~
// replaces string 32177 (UHMAY01.DLG)
@1396 = ~In fact...I have an item from an ancestor of mine... once the ranger protector for Imnesvale.  It is bound with magic as strong as any.~
// replaces string 32185 (UHMAY01.DLG)
@1397 = ~I am not sure if the item will be of any use to you.  Errrm...is...is this enough of a reward?  I suppose I could offer you some amount of gold, as well...~
// replaces string 32207 (UHMAY01.DLG)
@1398 = ~I will offer you the same as the knight... a fine magical item that belonged to my ranger ancestor!  Plus gold, if you require.  Will you help us?~
// replaces string 49560 (UHMAY01.DLG)
@1399 = ~Not so fast. The money you gave me before was an advance only. I expect more than some aged item. Pay me now before I become angry.~
// replaces string 32063 (UHMAY01.DLG)
@1449 = ~That was my ancestor, and I've kept it.  I'm told it's wrapped up with strong magics.  I'd be happy to offer it-- and some gold-- to save my village.~
// replaces string 5740 (ALDETH.DLG, Aldeth Sashenstar in Baldur's Gate; 205740 in EET)
@1649 = ~I must thank you for saving my consortium!  I will reward all of you greatly for what you have done for me.  I hope what I give you now is reward enough.  This item is a family heirloom, and I am sure you will find it useful.  Know that if ever you are in trouble, you can come to my estate.~
// replaces journal entry 26828 (ALDETH.DLG; 226828 in EET), below the game's own title line
@1650 = ~The estate was infested with doppelgangers! I have now cleaned them out. Aldeth Sashenstar has praised me for doing the job for him and is happy to welcome me into his estate if I am ever in trouble. I may yet need it.~

// languages/<folder>/ppdjinn.tra
// replaces string 48995 (PPDJINN.DLG, a djinni)
@99 = ~The stone commands and I must obey. This item is the gift to those of sound mind. A riddle. A reward. Thusly was my home built and I have fulfilled my duties. You are doing well, <GABBER>.~
```

## The original options' texts

Two of Item Randomiser's original options bring texts of their own: "Include Kangaxx
items" (Kangaxx's conversation, `hlskull.tra` @0-@21, and the ward stone, `in-game.tra`
@1500-@1504) and "Cursed disguise item" (the names and descriptions of the disguised
items, `in-game.tra` @200-@225, whose Enhanced Edition wording is in
`languages/bgee/english/in-game.tra`). English, Polish and German have them. In a game
in any other language these options show the English texts, so they work in every
game; a translation of all of an option's lines shows it in that language instead. An
option whose lines a translation has only in part stays English, so that its texts
never mix two languages. The English files are the template: translate each text,
keep the `@` numbers, and add female wordings as above. A cursed item's description
is best made from the game's own description of that item in your language (its
`dialog.tlk`): add only what Item Randomiser changes, the item it is disguised as and a
heading where the English has one, and use the game's item names. The German texts
are made this way (BG2:EE's `dialog.tlk`); only @210 and @211, Item Revisions'
descriptions, are translated from English. To check them, add the language to
`OPTIONS_TRANSLATED` in `tests/test_translations.py`.

## Checking a translation

With the translation in place, the configurator's check accepts the quests in a game of
that language. Without a game, add the language to `COMPLETE` in
`tests/test_translations.py` and run, from the repository root,
`python -m unittest discover -s tests -p test_translations.py`. A failure names a
missing line ("Item Randomiser's Spanish translation has no reward text @399 in
spanish/in-game.tra") or a file that is not in its code page.
