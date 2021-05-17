import operator
import random
from collections import defaultdict
from threading import Lock
from time import sleep, time
from typing import Dict, List, NamedTuple, TypeVar

from sqlalchemy import (
    Boolean,
    Column,
    Integer,
    PrimaryKeyConstraint,
    String,
    Table,
    and_,
    desc,
)
from sqlalchemy.sql import select


duck_tail = "・゜゜・。。・゜゜"
duck = [
    "\\_o< ",
    "\\_O< ",
    "\\_0< ",
    "\\_\u00f6< ",
    "\\_\u00f8< ",
    "\\_\u00f3< ",
]
duck_noise = ["QUACK!", "FLAP FLAP!", "quack!"]


class ScoreEntry(NamedTuple):
    network: str
    name: str
    chan: str
    shot: int = 0
    befriend: int = 0


class ChannelState:
    """
    Represents the state of the hunt in a single channel
    """

    def __init__(self):
        self.masks = []
        self.messages = 0
        self.game_on = False
        self.no_duck_kick = False
        self.duck_status = 0
        self.next_duck_time = 0
        self.duck_time = 0
        self.shoot_time = 0

    def clear_messages(self):
        self.messages = 0
        self.masks.clear()

    def should_deploy(self, conn):
        """Should we deploy a duck?"""
        msg_delay = get_config(conn, "minimum_messages", 10)
        mask_req = get_config(conn, "minimum_users", 5)
        return (
            self.game_on
            and self.duck_status == 0
            and self.next_duck_time <= time()
            and self.messages >= msg_delay
            and len(self.masks) >= mask_req
        )

    def handle_message(self, event):
        if self.game_on and self.duck_status == 0:
            self.messages += 1
            if event.host not in self.masks:
                self.masks.append(event.host)


T = TypeVar("T")
ConnMap = Dict[str, Dict[str, T]]
scripters: Dict[str, float] = defaultdict(float)
chan_locks: ConnMap[Lock] = defaultdict(lambda: defaultdict(Lock))
game_status: ConnMap[ChannelState] = defaultdict(
    lambda: defaultdict(ChannelState)
)
opt_out: Dict[str, List[str]] = defaultdict(list)
"""
self.game_status structure
{
    'guild.id':{
        'channel.id':{
            'duck_status':0|1|2,
            'next_duck_time':'integer',
            'game_on':0|1,
            'no_duck_kick': 0|1,
            'duck_time': 'float',
            'shoot_time': 'float',
            'messages': integer,
            'masks': list,
            'duck_message_id' : int
        }
    }
}
"""
#TODO: stats are not correctly notifying when no results (friends/killers)

class DuckHuntTable(database.base):
    __tablename__ = "duck_hunt"
    network = Column(String(), primary_key=True)
    name = Column(String(), primary_key=True)
    shot = Column(Integer())
    befriend = Column(Integer())
    chan = Column(String(), primary_key=True)

class NoHuntTable(database.base):
    __tablename__ = "nohunt"
    network = Column(String(), primary_key=True)
    chan = Column(String(), primary_key=True)

class StatusTable(database.base):
    __tablename__ = "duck_status"
    network = Column(String(), primary_key=True)
    chan = Column(String(), primary_key=True)
    active = Column(Boolean())
    duck_kick = Column(Boolean())

class Duckhunt(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.log = logging.getLogger("beeerbot")
        self.config = bot.config
        self.db = database.Session
        self.table = DuckHuntTable
        self.status_table = StatusTable
        self.nohunt_table = NoHuntTable

        self.delete_source_msg = False

        #Set up duck parts
        self.duck_tail = "・゜゜・。。・゜゜"
        self.duck = ["\_o< ", "\_O< ", "\_0< ", "\_\u00f6< ", "\_\u00f8< ", "\_\u00f3< "]
        self.duck_noise = ["QUACK!", "FLAP FLAP!", "quack!"]

        # Set up game status
        self.scripters = defaultdict(int)
        self.game_status = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
        # set optout status
        self.opt_out = []

        #chans = self.db.execute(select([self.optout.c.chan]))
        stmt = select(self.nohunt_table).order_by(self.nohunt_table.chan)
        for row in self.db.execute(stmt).scalars().all():
            chan = row.chan
            self.opt_out.append(chan)

        #set up duck times for all channels
        try:
            stmt = select(self.status_table)
            for row in self.db.execute(stmt).scalars().all():
                net = str(row.network)
                chan = str(row.chan)
                self.game_status[net][chan]["game_on"] = int(row.active)
                self.game_status[net][chan]["no_duck_kick"] = int(row.duck_kick)
                self.log.info("net:{} chan:{} active:{} duck_kick:{}".format(row.network, row.chan, int(row.active), int(row.duck_kick)))
                if self.game_status[net][chan]["game_on"] == int(1):
                    self.set_ducktime(chan, net)
        except:
            self.log.error('Failed set up duck times', exc_info=1)

        self.deploy_duck.start()
        # self.save_status.start()

        self.log.info("Duckhunt initialized")

    async def cog_command_error(self, ctx, error):
        return

    def cog_unload(self):
        # self.save_status.cancel()
        self.deploy_duck.cancel()


    @tasks.loop(seconds=10.0)
    async def deploy_duck(self):
        for network in self.game_status:
            for chan in self.game_status[network]:

                active = self.game_status[network][chan]['game_on']
                duck_status = self.game_status[network][chan]['duck_status']
                next_duck = self.game_status[network][chan]['next_duck_time']
                chan_messages = self.game_status[network][chan]['messages']
                chan_masks = self.game_status[network][chan]['masks']
                if active == 1 and duck_status == 0 and next_duck <= time() and chan_messages >= self.config.duckhunt_options.get('min_lines', 10) and len(chan_masks) >= self.config.duckhunt_options.get('min_users', 2):
                    # deploy a duck to channel
                    try:
                        self.game_status[network][chan]['duck_status'] = 1
                        self.game_status[network][chan]['duck_time'] = time()
                        dtail, dbody, dnoise = self.generate_duck()
                        channel = self.bot.get_channel(int(chan))

                        self.log.info("channel check: {}".format(repr(channel)))
                        em = discord.Embed(
                            title="a duck has appeared",
                            description="{}{}{}".format(dtail, dbody, dnoise),
                            color=469033)
                        em.set_thumbnail(url="https://i.imgur.com/2cY8l5R.png")
                        duck_message = await channel.send(embed=em)
                        self.game_status[network][chan]['duck_message_id'] = duck_message.id
                        self.log.info("deploying duck to {}".format(channel.name))
                    except:

                        self.log.error("error deploying duck to {}".format(chan, exc_info=1))

                # Leave this commented out for now. I haven't decided how to make ducks leave.
                # if active == 1 and duck_status == 1 and self.game_status[network][chan]['flyaway'] <= int(time()):
                #    conn.message(chan, "The duck flew away.")
                #    self.game_status[network][chan]['duck_status'] = 2
                #    set_ducktime(chan, conn)
                continue
            continue


    # @tasks.loop(seconds=300)
    # async def save_status(self):
    def save_status(self):
        try:
            for network in self.game_status:
                for chan, status in self.game_status[network].items():
                    active = bool(status['game_on'])
                    duck_kick = bool(status['no_duck_kick'])
                    stmt = update(self.status_table).where(
                        self.status_table.chan == chan,
                        self.status_table.network == network
                    ).values(
                        active=active, duck_kick=duck_kick
                    )
                    res = self.db.execute(stmt)
                    if not res.rowcount:
                        stmt = insert(self.status_table).values(
                            network=network,
                            chan=chan,
                            active=active,
                            duck_kick=duck_kick
                        )
                        self.db.execute(stmt)

            self.db.commit()
        except:
            self.log.error('Failed save_status', exc_info=1)


    def dbadd_entry(self, nick, channel_id: str, guild_id: str, shoot: int, friend: int):
        """Takes care of adding a new row to the database."""
        stmt = insert(self.table).values(
            network=guild_id,
            chan=channel_id,
            name=nick,
            shot=shoot,
            befriend=friend
        )
        self.db.execute(stmt)
        self.db.commit()


    def dbupdate(self, nick, channel_id: str, guild_id: str, shoot: int, friend: int):
        """update a db row"""
        if shoot and not friend:
            stmt = update(self.table).where(
                self.table.network == guild_id,
                self.table.chan == channel_id,
                self.table.name == nick
            ).values(
                shot=shoot
            )
        elif friend and not shoot:
            stmt = update(self.table).where(
                self.table.network == guild_id,
                self.table.chan == channel_id,
                self.table.name == nick
            ).values(
                befriend=friend
            )
        elif friend and shoot:
            stmt = update(self.table).where(
                self.table.network == guild_id,
                self.table.chan == channel_id,
                self.table.name == nick
            ).values(
                befriend=friend,
                shot=shoot
            )

        self.db.execute(stmt)
        self.db.commit()


    def set_ducktime(self, channel_id: str, guild_id: str):
        #TODO: move min and max duck times to config
        #TODO: calling duck time sets times for ALL channels, not caller channel
        next_duck = random.randint(int(time()) + 480, int(time()) + 3600)
        if int(channel_id) == 782794037831663639:
            self.log.info("setting time for test channel")
            next_duck = random.randint(int(time()) + 10, int(time()) + 20)

        self.game_status[guild_id][channel_id]['next_duck_time'] = next_duck
        # self.game_status[conn][chan]['flyaway'] = self.game_status[guild_id][chan]['next_duck_time'] + 600
        self.game_status[guild_id][channel_id]['duck_status'] = 0
        # let's also reset the number of messages said and the list of masks that have spoken.
        self.game_status[guild_id][channel_id]['messages'] = 0
        self.game_status[guild_id][channel_id]['masks'] = []
        self.log.info("ducktime of {} set for {} {}".format(next_duck-int(time()), guild_id, channel_id))
        return


    def generate_duck(self):
        """Try and randomize the duck message so people can't highlight on it/script against it."""
        if random.randint(1, 40) == 1:
            dtail = "8====D"
            dbody = "~~~"
            dnoise = "FAP FAP FAP!"
        else:
            rt = random.randint(1, len(self.duck_tail) - 1)
            dtail = self.duck_tail[:rt] + u' \u200b ' + self.duck_tail[rt:]

            dbody = random.choice(self.duck)
            rb = random.randint(1, len(dbody) - 1)
            dbody = dbody[:rb] + u'\u200b' + dbody[rb:]

            dnoise = random.choice(self.duck_noise)
            rn = random.randint(1, len(dnoise) - 1)
            dnoise = dnoise[:rn] + u'\u200b' + dnoise[rn:]
        return (dtail, dbody, dnoise)


    def hit_or_miss(self, deploy: int, shoot: int):
        """This function calculates if the befriend or bang will be successful."""
        if shoot - deploy < 1:
            return .05
        elif 1 <= shoot - deploy <= 7:
            out = random.uniform(.60, .75)
            return out
        else:
            return 1


    @commands.Cog.listener('on_message')
    # @hook.event([EventType.message, EventType.action], singlethread=True)
    async def incrementMsgCounter(self, msg):
        """Increment the number of messages said in an active game channel. Also keep track of the unique masks that are speaking."""
        guild_id = str(getattr(msg.guild, 'id', None))
        channel_id = str(getattr(msg.channel, 'id', None))
        author_id = str(getattr(msg.author, 'id', None))

        if channel_id in self.opt_out:
            return
        if self.game_status[guild_id][channel_id]['game_on'] == 1 and self.game_status[guild_id][channel_id]['duck_status'] == 0:
            self.game_status[guild_id][channel_id]['messages'] += 1
            if author_id not in self.game_status[guild_id][channel_id]['masks']:
                self.log.info("incrementMsgCounter added a new mask")
                self.game_status[guild_id][channel_id]['masks'].append(author_id)


    @commands.command(aliases=["starthunt"])
    # @hook.command("starthunt", autohelp=False, permissions=["chanop", "op", "botcontrol"])
    async def start_hunt(self, ctx):
        """This command starts a duckhunt in your channel, to stop the hunt use .stophunt"""
        guild_id = str(getattr(ctx.guild, 'id', None))
        channel_id = str(getattr(ctx.channel, 'id', None))
        channel_name = str(getattr(ctx.channel, 'name', None))

        if channel_id in self.opt_out:
            return
        elif ctx.guild is None:
            out = await ctx.send("Must be used in a channel")
            await out.delete(delay=5)
            return
        check = self.game_status[guild_id][channel_id]['game_on']
        if check:
            out = await ctx.send("there is already a game running in {}.".format(channel_name))
            await out.delete(delay=5)
            return
        else:
            self.game_status[guild_id][channel_id]['game_on'] = 1
        self.log.info("deploy_duck game info: {}".format(repr(self.game_status[guild_id][channel_id])))


        self.set_ducktime(channel_id, guild_id)
        self.save_status()
        return await ctx.send("Ducks have been spotted nearby. See how many you can shoot or save. use !bang to shoot or !befriend to save them. NOTE: Ducks now appear as a function of time and channel activity.")



    @commands.command(aliases=["stophunt"])
    async def stop_hunt(self, ctx):
        """This command stops the duck hunt in your channel. Scores will be preserved"""
        guild_id = str(getattr(ctx.guild, 'id', None))
        channel_id = str(getattr(ctx.channel, 'id', None))
        channel_name = str(getattr(ctx.channel, 'name', None))

        if channel_id in self.opt_out:
            return
        if self.game_status[guild_id][channel_id]['game_on']:
            self.game_status[guild_id][channel_id]['game_on'] = 0
            self.save_status()
            out = await ctx.send("the game has been stopped.")
            await out.delete(delay=20)
            if self.delete_source_msg:
                await ctx.message.delete(delay=10)
            return
        else:
            out = await ctx.send("There is no game running in {}.".format(channel_name))
            await out.delete(delay=20)
            if self.delete_source_msg:
                await ctx.message.delete(delay=10)
            return


    @commands.command(aliases=["duckmute"])
    async def no_duck_kick(self, ctx, text):
        """If the bot has OP or half-op in the channel you can specify .duckkick enable|disable so that people are kicked for shooting or befriending a non-existent goose. Default is off."""
        guild_id = str(getattr(ctx.guild, 'id', None))
        channel_id = str(getattr(ctx.channel, 'id', None))

        if channel_id in self.opt_out:
            return
        if text.lower() == 'enable':
            self.game_status[guild_id][channel_id]['no_duck_kick'] = 1
            out = await ctx.send("users will now be muted for shooting or befriending non-existent ducks. The bot needs to have appropriate flags to be able to mute users for this to work.")
            await out.delete(delay=20)
            if self.delete_source_msg:
                await ctx.message.delete(delay=10)
            return

        elif text.lower() == 'disable':
            self.game_status[guild_id][channel_id]['no_duck_kick'] = 0
            out = await ctx.send("muting for non-existent ducks has been disabled.")
            await out.delete(delay=20)
            if self.delete_source_msg:
                await ctx.message.delete(delay=10)
            return
        else:
            return


    @commands.command()
    async def bang(self, ctx):
        """when there is a duck on the loose use this command to shoot it."""
        guild_id = str(getattr(ctx.guild, 'id', None))
        channel_id = str(getattr(ctx.channel, 'id', None))
        channel_name = str(getattr(ctx.channel, 'name', None))
        author_name = str(getattr(ctx.author, 'name', None))

        if channel_id in self.opt_out:
            return

        score = ""
        out = ""
        miss = ["WHOOSH! You missed the duck completely!", "Your gun jammed!", "Better luck next time.",
                "WTF?! Who are you, Kim Jong Un firing missiles? You missed."]

        if not self.game_status[guild_id][channel_id]['game_on']:
            out = await ctx.send("There is no active hunt right now. Use !starthunt to start a game.")
            await out.delete(delay=20)
            if self.delete_source_msg:
                await ctx.message.delete(delay=10)
            return
        elif self.game_status[guild_id][channel_id]['duck_status'] != 1:
            #TODO: Mute
            #if self.game_status[guild_id][channel_id]['no_duck_kick'] == 1:
                #out = "KICK {} {} :There is no duck! What are you shooting at?".format(chan, nick)
            out = await ctx.send("There is no duck. What are you shooting at?")
            await out.delete(delay=20)
            if self.delete_source_msg:
                await ctx.message.delete(delay=10)
            return
        else:
            self.game_status[guild_id][channel_id]['shoot_time'] = time()
            deploy = self.game_status[guild_id][channel_id]['duck_time']
            shoot = self.game_status[guild_id][channel_id]['shoot_time']

            if author_name in self.scripters:
                if self.scripters[author_name] > shoot:
                    #TODO: DM?
                    out = await ctx.send("You are in a cool down period, you can try again in {} seconds.".format(
                        str(self.scripters[author_name] - shoot)
                    ))
                    await out.delete(delay=20)
                    if self.delete_source_msg:
                        await ctx.message.delete(delay=10)
                    return

            chance = self.hit_or_miss(deploy, shoot)
            if not random.random() <= chance and chance > .05:
                out = random.choice(miss) + " You can try again in 7 seconds."
                self.scripters[author_name] = shoot + 7
                out = await ctx.send(out)
                await out.delete(delay=10)
                if self.delete_source_msg:
                    await ctx.message.delete(delay=10)
                return
            if chance == .05:
                out += "You pulled the trigger in {} seconds, that's mighty fast. Are you sure you aren't a script? Take a 2 hour cool down.".format(str(shoot - deploy))
                self.scripters[author_name] = shoot + 7200
                if not random.random() <= chance:
                    out = await ctx.send(random.choice(miss) + " " + out)

                out = await ctx.send(out)
                await out.delete(delay=20)
                if self.delete_source_msg:
                    await ctx.message.delete(delay=10)
                return

            self.game_status[guild_id][channel_id]['duck_status'] = 2
            stmt = select(self.table.shot).where(
                self.table.network == guild_id,
                self.table.chan == channel_id,
                self.table.name == author_name
            )
            score = self.db.execute(stmt).one()
            if score:
                self.log.info("score present")
                score = score[0]
                score += 1
                self.dbupdate(author_name, channel_id, guild_id, score, 0)
            else:
                self.log.info("new player scored")
                score = 1
                self.dbadd_entry(author_name, channel_id, guild_id, score, 0)

            timer = "{:.3f}".format(shoot - deploy)
            duck = "duck" if score == 1 else "ducks"
            # https://i.imgur.com/0Eyajax.png

            out = await ctx.send("{} you shot a duck in {} seconds! You have killed {} {} in {}.".format(author_name, timer, score, duck, channel_name))

            await out.delete(delay=30)
            if self.delete_source_msg:
                await ctx.message.delete(delay=10)


            em = discord.Embed(
                title="this duck has been murdered",
                description="rest in peace little ducky",
                color=996666)
            em.set_thumbnail(url="https://i.imgur.com/0Eyajax.png")
            em.set_footer(text="{} pulled the trigger in {} seconds".format(author_name, timer))

            duck_message = await ctx.channel.fetch_message(self.game_status[guild_id][channel_id]['duck_message_id'])
            await duck_message.edit(embed=em)
            self.set_ducktime(channel_id, guild_id)

    @commands.command(aliases=["bef"])
    async def befriend(self, ctx):
        """when there is a duck on the loose use this command to befriend it before someone else shoots it."""
        guild_id = str(getattr(ctx.guild, 'id', None))
        channel_id = str(getattr(ctx.channel, 'id', None))
        channel_name = str(getattr(ctx.channel, 'name', None))
        author_name = str(getattr(ctx.author, 'name', None))

        if channel_id in self.opt_out:
            return

        out = ""
        score = ""
        miss = ["The duck didn't want to be friends, maybe next time.",
                "Well this is awkward, the duck needs to think about it.",
                "The duck said no, maybe bribe it with some pizza? Ducks love pizza don't they?",
                "Who knew ducks could be so picky?"]
        if not self.game_status[guild_id][channel_id]['game_on']:
            out = await ctx.send("There is no hunt right now. Use !starthunt to start a game.")
            await out.delete(delay=30)
            if self.delete_source_msg:
                await ctx.message.delete(delay=10)
            return
        elif self.game_status[guild_id][channel_id]['duck_status'] != 1:
            #TODO: Mute
            #if self.game_status[guild_id][channel_id]['no_duck_kick'] == 1:
                #out = "KICK {} {} :You tried befriending a non-existent duck. That's fucking creepy.".format(chan, nick)

            out = await ctx.send("You tried befriending a non-existent duck. That's freaking creepy.")
            await out.delete(delay=20)
            if self.delete_source_msg:
                await ctx.message.delete(delay=10)
            return
        else:
            self.game_status[guild_id][channel_id]['shoot_time'] = time()
            deploy = self.game_status[guild_id][channel_id]['duck_time']
            shoot = self.game_status[guild_id][channel_id]['shoot_time']
            if author_name in self.scripters:
                if self.scripters[author_name] > shoot:
                    out = await ctx.send("You are in a cool down period, you can try again in {} seconds.".format(str(self.scripters[author_name] - shoot)))

                    await out.delete(delay=20)
                    if self.delete_source_msg:
                        await ctx.message.delete(delay=10)
                    return

            chance = self.hit_or_miss(deploy, shoot)
            if not random.random() <= chance and chance > .05:
                out = random.choice(miss) + " You can try again in 7 seconds."
                self.scripters[author_name] = shoot + 7
                out = await ctx.send(out)
                await out.delete(delay=10)
                if self.delete_source_msg:
                    await ctx.message.delete(delay=10)
                return
            if chance == .05:
                out = "You tried friending that duck in {} seconds, that's mighty fast. Are you sure you aren't a script? Take a 2 hour cool down.".format(str(shoot - deploy))
                self.scripters[author_name] = shoot + 7200
                if not random.random() <= chance:
                    out = await ctx.send(random.choice(miss) + " " + out)
                    await out.delete(delay=20)
                    if self.delete_source_msg:
                        await ctx.message.delete(delay=10)
                    return

                else:
                    out = await ctx.send(out)
                    await out.delete(delay=20)
                    if self.delete_source_msg:
                        await ctx.message.delete(delay=10)
                    return

            self.game_status[guild_id][channel_id]['duck_status'] = 2

            stmt = select(self.table.befriend).where(
                self.table.network == guild_id,
                self.table.chan == channel_id,
                self.table.name == author_name
            )
            score = self.db.execute(stmt).one()

            if score:
                score = score[0]
                score += 1
                self.dbupdate(author_name, channel_id, guild_id, 0, score)
            else:
                score = 1
                self.dbadd_entry(author_name, channel_id, guild_id, 0, score)
            duck = "duck" if score == 1 else "ducks"
            timer = "{:.3f}".format(shoot - deploy)
            # https://i.imgur.com/XF11gK4.png
            out = await ctx.send(
                "{} you befriended a duck in {} seconds! You have made friends with {} {} in {}.".format(author_name, timer, score, duck, channel_name))

            await out.delete(delay=30)
            if self.delete_source_msg:
                await ctx.message.delete(delay=60)

            em = discord.Embed(
                title="this duck has been befriended",
                description="fly on little ducky",
                color=996666)
            em.set_thumbnail(url="https://i.imgur.com/XF11gK4.png")
            em.set_footer(text="{} seduced it in {} seconds".format(author_name, timer))

            duck_message = await ctx.channel.fetch_message(self.game_status[guild_id][channel_id]['duck_message_id'])
            await duck_message.edit(embed=em)
            self.set_ducktime(channel_id, guild_id)


    def smart_truncate(self, content, length=2000, suffix='...'):
        if len(content) <= length:
            return content
        else:
            return content[:length].rsplit(' • ', 1)[0] + suffix


    @commands.command()
    # @hook.command("friends", autohelp=False)
    async def friends(self, ctx):
        """Prints a list of the top duck friends in the channel, if 'global' is specified all channels in the database are included."""
        guild_id = str(getattr(ctx.guild, 'id', None))
        channel_id = str(getattr(ctx.channel, 'id', None))
        channel_name = str(getattr(ctx.channel, 'name', None))
        text = str(getattr(ctx.message, 'content'))[9:]

        if channel_id in self.opt_out:
            return

        friends = defaultdict(int)
        chancount = defaultdict(int)
        out = ""
        if text.lower() == 'global' or text.lower() == 'average':
            out = "Duck friend scores across the network: "
            stmt = select(self.table).where(
                self.table.network == guild_id
            ).order_by(desc(self.table.befriend))
            scores = self.db.execute(stmt).scalars().all()
            if scores:
                for row in scores:
                    if row.befriend == 0:
                        continue
                    chancount[row.chan] += 1
                    friends[row.chan] += row.befriend
                if text.lower() == 'average':
                    for k, v in friends.items():
                        friends[k] = int(v / chancount[k])
            else:
                out = await ctx.send("it appears no on has friended any ducks yet.")
                await out.delete(delay=20)
                if self.delete_source_msg:
                    await ctx.message.delete(delay=10)
                return
        else:
            out = "Duck friend scores in {}: ".format(channel_name)
            stmt = select(self.table).where(
                self.table.network == guild_id,
                self.table.chan == channel_id
            ).order_by(desc(self.table.befriend))
            scores = self.db.execute(stmt).scalars().all()
            if scores:
                for row in scores:
                    if row.befriend == 0:
                        continue
                    friends[row.chan] += row.befriend
            else:
                out = await ctx.send("it appears no on has friended any ducks yet.")
                await out.delete(delay=20)
                if self.delete_source_msg:
                    await ctx.message.delete(delay=10)
                return

        try:
            paginator = DiscordUtils.Pagination.AutoEmbedPaginator(ctx, auto_footer=True, remove_reactions=True, timeout=60)
            topfriends = sorted(friends.items(), key=operator.itemgetter(1), reverse=True)

            count = len(topfriends)
            pages = []
            page_no = 0
            i = 1

            while count > 0:
                field1=topfriends[:10]
                field1value = ""
                field2value = ""
                for k, v in field1:
                    if i % 10 == int(0):
                        newline = ""
                    else:
                        newline = "\n"
                    field1value += "{}. {}: {}{}".format(str(i), k, str(v), newline)
                    i += 1
                rank_1_1 = ((page_no*2)*10)+1
                rank_1_2 = ((page_no*2)+1)*10
                field1title="{} - {}".format(rank_1_1, rank_1_2)
                del topfriends[:10]
                count -= 10
                if count > 10:
                    field2=topfriends[:10]
                    rank_2_1 = rank_1_1+10
                    rank_2_2 = rank_1_2+10
                    del topfriends[:10]
                elif count > 0:
                    remaining = len(topfriends)
                    field2=topfriends[:]
                    rank_2_1 = rank_1_1+10
                    rank_2_2 = rank_1_2+remaining
                    del topfriends

                for k, v in field2:
                    if i % 10 == int(0):
                        newline = ""
                    else:
                        newline = "\n"
                    field2value += "{}. {}: {}{}".format(str(i), k, str(v), newline)
                    i += 1
                field2title="{} - {}".format(rank_2_1, rank_2_2)

                page = discord.Embed(title="duck friends scoreboard",description=out, color=356839) \
                            .add_field(name=field1title, value=field1value) \
                            .add_field(name=field2title, value=field2value)
                page.set_footer(text="Use the emojis to change pages")

                pages.append(page)
                count -= 10
                page_no += 1
            out = await paginator.run(pages)
            await out.delete(delay=300)
            if self.delete_source_msg:
                await ctx.message.delete(delay=10)
            return
        except:
            self.log.error('Failed to paginate', exc_info=1)


    @commands.command()
    # @hook.command("killers", autohelp=False)
    async def killers(self, ctx):
        """Prints a list of the top duck killers in the channel, if 'global' is specified all channels in the database are included."""
        guild_id = str(getattr(ctx.guild, 'id', None))
        channel_id = str(getattr(ctx.channel, 'id', None))
        channel_name = str(getattr(ctx.channel, 'name', None))
        text = str(getattr(ctx.message, 'content'))[9:]

        if channel_id in self.opt_out:
            return


        killers = defaultdict(int)
        chancount = defaultdict(int)
        out = ""
        if text.lower() == 'global' or text.lower() == 'average':
            out = "Duck killer scores across the server: "
            stmt = select(self.table).where(
                self.table.network == guild_id
            ).order_by(desc(self.table.shot))
            scores = self.db.execute(stmt).scalars().all()
            if scores:
                for row in scores:
                    if row.shot == 0:
                        continue
                    chancount[row.chan] += 1
                    killers[row.chan] += row.shot
                if text.lower() == 'average':
                    for k, v in killers.items():
                        killers[k] = int(v / chancount[k])
            else:
                out = await ctx.send("it appears no on has killed any ducks yet.")
                await out.delete(delay=20)
                if self.delete_source_msg:
                    await ctx.message.delete(delay=10)
                return
        else:
            out = "Duck killer scores in {}: ".format(channel_name)
            stmt = select(self.table).where(
                self.table.network == guild_id,
                self.table.chan == channel_id
            ).order_by(desc(self.table.shot))
            scores = self.db.execute(stmt).scalars().all()
            if scores:
                for row in scores:
                    if row.shot == 0:
                        continue
                    killers[row.chan] += row.shot
                    self.log.info('adding: {} +{}'.format(row.chan, row.shot))
            else:
                self.log.info('scores unset')
                out = await ctx.send("it appears no one has killed any ducks yet.")
                await out.delete(delay=20)
                if self.delete_source_msg:
                    await ctx.message.delete(delay=10)
                return
        try:
            paginator = DiscordUtils.Pagination.AutoEmbedPaginator(ctx, auto_footer=True, remove_reactions=True, timeout=60)
            topkillers = sorted(killers.items(), key=operator.itemgetter(1), reverse=True)

            count = len(topkillers)
            pages = []
            page_no = 0
            i = 1

            while count > 0:
                self.log.info('count: {}'.format(count))
                field1=topkillers[:10]
                field1value = ""
                field2value = ""
                for k, v in field1:
                    if i % 10 == int(0):
                        newline = ""
                    else:
                        newline = "\n"
                    field1value += "{}. {}: {}{}".format(str(i), k, str(v), newline)
                    i += 1
                rank_1_1 = ((page_no*2)*10)+1
                rank_1_2 = ((page_no*2)+1)*10
                field1title="{} - {}".format(rank_1_1, rank_1_2)
                del topkillers[:10]
                count -= 10
                if count > 10:
                    field2=topkillers[:10]
                    rank_2_1 = rank_1_1+10
                    rank_2_2 = rank_1_2+10
                    del topkillers[:10]
                elif count > 0:
                    remaining = len(topkillers)
                    field2=topkillers[:]
                    rank_2_1 = rank_1_1+10
                    rank_2_2 = rank_1_2+remaining
                    del topkillers

                for k, v in field2:
                    if i % 10 == int(0):
                        newline = ""
                    else:
                        newline = "\n"
                    field2value += "{}. {}: {}{}".format(str(i), k, str(v), newline)
                    i += 1
                field2title="{} - {}".format(rank_2_1, rank_2_2)

                page = discord.Embed(title="duck killers scoreboard",description=out, color=356839) \
                            .add_field(name=field1title, value=field1value) \
                            .add_field(name=field2title, value=field2value)
                page.set_footer(text="Use the emojis to change pages")

                pages.append(page)
                count -= 10
                page_no += 1
            out = await paginator.run(pages)
            await out.delete(delay=300)
            if self.delete_source_msg:
                await ctx.message.delete(delay=10)
            return
        except:
            self.log.error('Failed to paginate', exc_info=1)




    @commands.command()
    # @hook.command("duckmerge", permissions=["botcontrol"])
    async def duckmerge(self, ctx):
        """Moves the duck scores from one nick to another nick. Accepts two nicks as input the first will have their duck scores removed the second will have the first score added. Warning this cannot be undone."""
        guild_id = str(getattr(ctx.guild, 'id', None))
        text = str(getattr(ctx.message, 'content'))[11:]

        oldnick, newnick = text.split()
        if not oldnick or not newnick:
            out = await ctx.send("Please specify two nicks for this command.")
            await out.delete(delay=20)
            if self.delete_source_msg:
                await ctx.message.delete(delay=10)
            return
        stmt = select(self.table).where(
            self.table.network == guild_id,
            self.table.name == oldnick
        ).order_by(desc(self.table.shot))
        oldnickscore = self.db.execute(stmt).scalars().all()

        stmt = select(self.table).where(
            self.table.network == guild_id,
            self.table.name == oldnick
        ).order_by(desc(self.table.shot))
        newnickscore = self.db.execute(stmt).scalars().all()

        duckmerge = defaultdict(lambda: defaultdict(int))
        duckmerge["TKILLS"] = 0
        duckmerge["TFRIENDS"] = 0
        channelkey = {"update": [], "insert": []}
        if oldnickscore:
            if newnickscore:
                for row in newnickscore:
                    duckmerge[row.chan]["shot"] = row.shot
                    duckmerge[row.chan]["befriend"] = row.befriend
                for row in oldnickscore:
                    if row.chan in duckmerge:
                        duckmerge[row.chan]["shot"] = duckmerge[row.chan]["shot"] + row.shot
                        duckmerge[row.chan]["befriend"] = duckmerge[row.chan]["befriend"] + row.befriend
                        channelkey["update"].append(row.chan)
                        duckmerge["TKILLS"] = duckmerge["TKILLS"] + row.shot
                        duckmerge["TFRIENDS"] = duckmerge["TFRIENDS"] + row.befriend
                    else:
                        duckmerge[row.chan]["shot"] = row.shot
                        duckmerge[row.chan]["befriend"] = row.befriend
                        channelkey["insert"].append(row.chan)
                        duckmerge["TKILLS"] = duckmerge["TKILLS"] + row.shot
                        duckmerge["TFRIENDS"] = duckmerge["TFRIENDS"] + row.befriend
            else:
                for row in oldnickscore:
                    duckmerge[row.chan]["shot"] = row.shot
                    duckmerge[row.chan]["befriend"] = row.befriend
                    channelkey["insert"].append(row.chan)
                    # TODO: Call self.dbupdate() and db_add_entry for the items in duckmerge
            for channel in channelkey["insert"]:
                self.dbadd_entry(newnick, channel, guild_id, duckmerge[channel]["shot"], duckmerge[channel]["befriend"])
            for channel in channelkey["update"]:
                self.dbupdate(newnick, channel, guild_id, duckmerge[channel]["shot"], duckmerge[channel]["befriend"])

            stmt = delete(self.table).where(
                self.table.network == guild_id,
                self.table.name == oldnick
            )
            self.db.execute(stmt)
            self.db.commit()
            out = await ctx.send("Migrated {} duck kills and {} duck friends from {} to {}".format(duckmerge["TKILLS"],
                                                                                    duckmerge["TFRIENDS"], oldnick,
                                                                                    newnick))
            await out.delete(delay=20)
            if self.delete_source_msg:
                await ctx.message.delete(delay=10)
            return
        else:
            out = await ctx.send("There are no duck scores to migrate from {}".format(oldnick))
            await out.delete(delay=20)
            if self.delete_source_msg:
                await ctx.message.delete(delay=10)
            return


    @commands.command()
    # @hook.command("ducks", autohelp=False)
    async def ducks(self, ctx):
        """Prints a users duck stats. If no nick is input it will check the calling username."""
        guild_id = str(getattr(ctx.guild, 'id', None))
        channel_id = str(getattr(ctx.channel, 'id', None))
        channel_name = str(getattr(ctx.channel, 'name', None))
        text = str(getattr(ctx.message, 'content'))[6:]

        if text:
            name = text.split()[0]
        else:
            name = str(getattr(ctx.author, 'name', None))
        ducks = defaultdict(int)

        stmt = select(self.table).where(
            self.table.network == guild_id,
            self.table.name == name
        )
        scores = self.db.execute(stmt).scalars().all()

        if scores:
            for row in scores:
                if row.chan == channel_id:
                    ducks["chankilled"] += row.shot
                    ducks["chanfriends"] += row.befriend
                ducks["killed"] += row.shot
                ducks["friend"] += row.befriend
                ducks["chans"] += 1
            if ducks["chans"] == 1:
                out = await ctx.send("*{}* has killed {} and befriended {} ducks in **{}**.".format(name, ducks["chankilled"],
                                                                                ducks["chanfriends"], channel_name))
                await out.delete(delay=60)
                if self.delete_source_msg:
                    await ctx.message.delete(delay=10)
                return
            kill_average = int(ducks["killed"] / ducks["chans"])
            friend_average = int(ducks["friend"] / ducks["chans"])
            out = await ctx.send(
                "*{}'s* duck stats: {} killed and {} befriended in **{}**. Across {} channels: {} killed and {} befriended. Averaging {} kills and {} friends per channel.".format(
                    name, ducks["chankilled"], ducks["chanfriends"], channel_name, ducks["chans"], ducks["killed"], ducks["friend"],
                    kill_average, friend_average))
            await out.delete(delay=300)
            if self.delete_source_msg:
                await ctx.message.delete(delay=10)
            return
        else:
            out = await ctx.send("It appears *{}* has not participated in the duck hunt.".format(name))
            await out.delete(delay=20)
            if self.delete_source_msg:
                await ctx.message.delete(delay=10)
            return


    @commands.command()
    # @hook.command("duckstats", autohelp=False)
    async def duckstats(self, ctx):
        """Prints duck statistics for the entire channel and totals for the network."""
        guild_id = str(getattr(ctx.guild, 'id', None))
        channel_id = str(getattr(ctx.channel, 'id', None))
        channel_name = str(getattr(ctx.channel, 'name', None))

        ducks = defaultdict(int)
        stmt = select(self.table).where(
            self.table.network == guild_id
        )
        scores = self.db.execute(stmt).scalars().all()
        if scores:
            ducks["friendchan"] = defaultdict(int)
            ducks["killchan"] = defaultdict(int)
            for row in scores:
                ducks["friendchan"][row.chan] += row.befriend
                ducks["killchan"][row.chan] += row.shot
                # ducks["chans"] += 1
                if row.chan == channel_id:
                    ducks["chankilled"] += row.shot
                    ducks["chanfriends"] += row.befriend
                ducks["killed"] += row.shot
                ducks["friend"] += row.befriend
            ducks["chans"] = int((len(ducks["friendchan"]) + len(ducks["killchan"])) / 2)
            killerchan, killscore = sorted(ducks["killchan"].items(), key=operator.itemgetter(1), reverse=True)[0]
            friendchan, friendscore = sorted(ducks["friendchan"].items(), key=operator.itemgetter(1), reverse=True)[0]
            out = await ctx.send(
                "*Duck Stats*: {} killed and {} befriended in **{}**. Across {} channels {} ducks have been killed and {} befriended. *Top Channels:* **{}** with {} kills and **{}** with {} friends".format(
                    ducks["chankilled"], ducks["chanfriends"], channel_name, ducks["chans"], ducks["killed"], ducks["friend"],
                    self.bot.get_channel(int(killerchan)).name, killscore, self.bot.get_channel(int(friendchan)).name, friendscore))
            await out.delete(delay=300)
            if self.delete_source_msg:
                await ctx.message.delete(delay=10)
            return
        else:
            out = await ctx.send( "It looks like there has been no duck activity on this channel or network.")
            await out.delete(delay=20)
            if self.delete_source_msg:
                await ctx.message.delete(delay=10)
            return

def setup(bot):
    bot.add_cog(Duckhunt(bot))


# @hook.command("duckforgive", permissions=["op", "ignore"])
# def duckforgive(text):
    # """Allows people to be removed from the mandatory cooldown period."""
    # global self.scripters
    # if text.lower() in self.scripters and self.scripters[text.lower()] > time():
        # self.scripters[text.lower()] = 0
        # return "{} has been removed from the mandatory cooldown period.".format(text)
    # else:
        # return "I couldn't find anyone banned from the hunt by that nick"


# @hook.command("hunt_opt_out", permissions=["op", "ignore"], autohelp=False)
# def hunt_opt_out(text, chan, db, conn):
    # """Running this command without any arguments displays the status of the current channel. hunt_opt_out add #channel will disable all duck hunt commands in the specified channel. hunt_opt_out remove #channel will re-enable the game for the specified channel."""
    # if not text:
        # if chan in opt_out:
            # return "Duck hunt is disabled in {}. To re-enable it run .hunt_opt_out remove #channel".format(chan)
        # else:
            # return "Duck hunt is enabled in {}. To disable it run .hunt_opt_out add #channel".format(chan)
    # if text == "list":
        # return ", ".join(opt_out)
    # if len(text.split(' ')) < 2:
        # return "please specify add or remove and a valid channel name"
    # command = text.split()[0]
    # channel = text.split()[1]
    # if not channel.startswith('#'):
        # return "Please specify a valid channel."
    # if command.lower() == "add":
        # if channel in opt_out:
            # return "Duck hunt has already been disabled in {}.".format(channel)
        # query = optout.insert().values(
            # network=guild_id,
            # chan=channel.lower())
        # self.db.execute(query)
        # self.db.commit()
        # load_optout(db)
        # return "The duckhunt has been successfully disabled in {}.".format(channel)
    # if command.lower() == "remove":
        # if not channel in opt_out:
            # return "Duck hunt is already enabled in {}.".format(channel)
        # delete = optout.delete(optout.c.chan == channel.lower())
        # self.db.execute(delete)
        # self.db.commit()
        # load_optout(db)
