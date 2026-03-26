- 1. Django Admin Unfold: Es werden keine Tasten zum hinzufuegen oben rechts in der Tabelle angezeigt. Es gibt nur Taste Hinzufuegen in der Menueleiste rechts wen es Mobilansicht angeschaltete ist
- 2. Snapadmin gib moeglichkeite, dass die Ta ellenzeilen klickbar sind und man direkt in die Detailansicht kommt. Aktuell ist nur fuer die Modelle die ueber SnapModel initialisiert sind, es soll aber fuer ganze Adminoberflaeche wirksam sein, wenn es snapadmin ald module hinzugefuegt ist, wenn es aber nicht so ist und nur die Modele ueber SNapModel initialisiert sind, soll weiter nur fuer Modele so funktionieren
- 3. In Swagger in Modelschemas sollen noch alle filter fuer alle Felder hinzugefuegt werden, damit man die Modelle filtern kann. Alles soll automatisch hinzugefuegt werden
- 4. Die Titels von Felder in Editiermaske sollen oben von Felder sein und nicht links daneben.
- 5. Einige Felder als z.B. Text Field, WYSIWYG Field sind zu breit und muessen nicht hinter dem Bildschirm angezeigt werden.
- 6. Der Abstand zwischen Border und Felder ist zu klein, soll schoener sein. Es soll nicht so eng sein.
- 7. Celery werden nicht gestartet. 
Error: 

Unable to load celery application.

Module 'sandbox' has no attribute 'celery'

Usage: celery [OPTIONS] COMMAND [ARGS]...

Try 'celery --help' for help.


Error: 

Unable to load celery application.

Module 'sandbox' has no attribute 'celery'
- 8. DB_ONLY/DUAL/ES_ONLY - in docs/index.html fehlt beschreibung wie man es einstellt und wie man Queries macht

- 9. Ich brauche, dass meine DjangoAdmin in Offlinemodus funktioniert, auch wenn es keine Internetverbindung hat. Es soll alles lokal laufen. Aber dieses Modus soll optional sein. Es soll nicht standardmaessig eingeschaltet sein. Man soll es in den Einstellungen einschalten koennen. Und es soll auch moeglich sein, dass man es fuer einzelne Modelle einschalten kann. Es soll auch moeglich sein, dass man es fuer einzelne Modelle ausschalten kann. Es soll auch moeglich sein, dass man es fuer einzelne Modelle ausschalten kann. Es soll auch moeglich sein, dass man es fuer einzelne Modelle ausschalten kann. Alles soll in indexedDb oder LocalStorage gesichert werden. Und wenn es wieder Internetverbindung gibt, soll es automatisch synchronisiert werden