# The real applet behind pyscard

The browser simulator answers APDUs with cards written in Python. This answers
them with the SeedKeeper applet's own Java, running on a JavaCard runtime in
jCardSim, so what gets tested is the code that gets flashed.

Everything above `simulated_card.py` is the browser simulator's package
unchanged. One seam, three backends:

    browser      pyscard surface -> cards written in Python
    this rig     pyscard surface -> the real applet in jCardSim
    a device     real pyscard    -> the card in your hand

Because the seam is pyscard rather than the wallet's own card calls, the whole
of pysatochip and the whole of SeedSigner run unmodified and unaware. That is
the difference between this and the Python test double in
`doomsigner/tests/test_musig2_card.py`, which replaces pysatochip's
CardConnector and so tests the wallet's protocol without testing pysatochip or
the applet.

## Running it

    JAVA_HOME=/usr/lib/jvm/java-8-openjdk-amd64
    JCARDSIM_JAR=<jcardsim>/target/jcardsim-3.0.5-SNAPSHOT.jar
    APPLET_SRC=<clone of bitsagarob/Seedkeeper-Applet, branch musig2-nonce-vault>
    PYTHONPATH=<this directory>

jCardSim must be 3.0.5-SNAPSHOT: it is the first version implementing
ALG_EC_SVDP_DH_PLAIN_XY, which the applet asks for at install time, so anything
older cannot even install it. Build it with JDK 8 and the jc305u4 SDK:

    JC_CLASSIC_HOME=<oracle_javacard_sdks>/jc305u4_kit mvn -DskipTests package

Compile AppletPipe first, which `<APPLET_SRC>/test/run.sh` does.

## One difference from the browser worth knowing

The real applet sets `needs_secure_channel` and never clears it, so every
instruction but GET STATUS and the two that set the channel up is encrypted.
The browser simulator reports that flag as 0 and skips the whole mechanism, so
a card there is easier to talk to than a card here, and code that works there
can still fail against a real card.
