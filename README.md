# SimpleStockWorker

This is a simple stock strategy manager which you can use to get daily report on next step suggestion for your investment!

Steps to Deploy It Yourself:
- write your own ```backtrader``` stategy and debug locally
- replace your strategy code in ```engine.py```
- set up a telegram bot (or other message service) in ```main.py```
- set up Github Actions to run on a daily basis, or when you commit

This is a demo strategy to hedge the risk of QQQ when there's the potential drawdown: See [https://danieldata.com/SimpleStockWorker/](https://danieldata.com/SimpleStockWorker/)

It's a good example to show how your strategy works historically, and what's your actionable suggestions for your portfolio.

Any questions or suggestions? use Issue or Discussion!

# Planned Features

## message notification

I plan to move telegram notifications from main.py to a single file, named "message" module.

## mutiple strategy

It's a good question that when you have more than 1 strategy, you don't want to have it with seperate repos. I would like to create more subpages for different strategies. You gonna name strategies individually, I hope to manage them like the jekyll-blog.

# AI Contribution

I am not a front-end guy, mostly data-end or back-end. I utilize Claude to help with the front-end and Nodejs components. I appreciate what Claude helped in this project. 

